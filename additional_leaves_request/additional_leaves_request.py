# -*- coding: utf-8 -*-
# Copyright (c) 2019, Mujadidia Inc and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import cint, cstr, date_diff, flt, formatdate, getdate, get_link_to_form, \
	comma_or, get_fullname, add_days, nowdate
from erpnext.hr.utils import set_employee_name, get_leave_period
from erpnext.hr.doctype.leave_block_list.leave_block_list import get_applicable_block_dates
from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee
from erpnext.buying.doctype.supplier_scorecard.supplier_scorecard import daterange
from mujadidia_hr.mujadidia_hr.doctype.leave_application.leave_application import get_holidays, get_leave_balance_on, is_lwp
from datetime import datetime
from datetime import timedelta

class LeaveDayBlockedError(frappe.ValidationError): pass
class OverlapError(frappe.ValidationError): pass
class AttendanceAlreadyMarkedError(frappe.ValidationError): pass
class NotAnOptionalHoliday(frappe.ValidationError): pass

from frappe.model.document import Document


allocated = ''
additional_leaves = 0

class AdditionalLeavesRequest(Document):

	def validate(self):
		set_employee_name(self)
		self.validate_request_options()
		self.validate_dates()
		self.validate_lwp()
		self.validate_balance_leaves()
		self.validate_allocated_leaves()

	def validate_dates(self):
		if self.from_date and self.to_date and (getdate(self.to_date) < getdate(self.from_date)):
			frappe.throw(_("To date cannot be before from date"))

	def on_update(self):
		if self.workflow_state == "Applied" and self.docstatus < 1:
			# notify leave approver about creation
			self.notify_leave_approver()
		if self.workflow_state == "Approved by Leader" and self.docstatus < 1:
			self.notify_employee()
		if self.workflow_state == "Rejected" and self.docstatus < 1:
			self.notify_employee()

	def on_cancel(self):
		if self.docstatus == 2:
			self.workflow_state = 'Rejected'
			self.cancel_leave_application()
			self.cancel_leave_allocation()

	def cancel_leave_allocation(self):
		if additional_leaves != 0:
			allo = frappe.get_doc('Leave Allocation', allocated)
			allo.new_leaves_allocated -= additional_leaves
			allo.save()

	
	def cancel_leave_application(self):
		app_name = "'%" + self.name + "%'"
		app = frappe.db.sql("select name from `tabLeave Application` WHERE description LIKE" + app_name, as_dict=1)
		for name in app:
			frappe.db.set_value("Leave Application", name, "workflow_state", 'Rejected')
			frappe.db.set_value("Leave Application", name, "docstatus", '2')
			a = frappe.get_doc("Leave Application", name)
			attendance = frappe.db.sql("""select name from `tabAttendance` where employee = %s\
				and (attendance_date between %s and %s) and docstatus < 2 and status in ('On Leave', 'Half Day')""",(a.employee, a.from_date, a.to_date), as_dict=1)
			for name in attendance:
				frappe.db.set_value("Attendance", name, "docstatus", 2)
				b = frappe.get_doc("Attendance", name)
				b.delete()

	def on_submit(self):
		if self.status == "Open":
			frappe.throw(_("Only Leave Applications with status 'Approved' and 'Rejected' can be submitted"))
		if self.status == "Approved":
			if self.request_for_paid_leave:
				allocated = self.validate_allocated_leaves()
				self.allocate_leaves(allocated)
				leave_app = self.generate_leave_application(self.from_date, self.to_date, self.leave_type)
				frappe.msgprint(_("Leave Application {0} has been Approved for this request").format(leave_app))
			if self.request_for_unpaid_leave and not self.adjust_allow:
				if self.leave_balance <= 0:
					leave_app = self.generate_leave_application(self.from_date, self.to_date, 'Leave Without Pay')
					frappe.msgprint(_("Leave Application {0} has been Approved for this request").format(leave_app))
				else:
					self.generate_unpaid_leaves()
			elif self.request_for_unpaid_leave and self.adjust_allow:
				sick_leave_balance = get_leave_balance_on(self.employee, 'Sick Leave', self.from_date, docname=self.name,
					consider_all_leaves_in_the_allocation_period=True)
				casual_leave_balance = get_leave_balance_on(self.employee, 'Casual Leave', self.from_date, docname=self.name,
					consider_all_leaves_in_the_allocation_period=True)
				vacation_leave_balance = get_leave_balance_on(self.employee, 'Vacation Leave', self.from_date, docname=self.name,
					consider_all_leaves_in_the_allocation_period=True)
				if sick_leave_balance == 0 and casual_leave_balance == 0 and vacation_leave_balance == 0:
					leave_app = self.generate_leave_application(self.from_date, self.to_date, 'Leave Without Pay')
					frappe.msgprint(_("Leave Application {0} has been Approved for this request").format(leave_app))
				else:
					self.generate_adjustable_leaves(sick_leave_balance, casual_leave_balance, vacation_leave_balance)
		self.notify_employee()
		self.reload()

	def validate_request_options(self):
		if self.request_for_paid_leave and self.request_for_unpaid_leave:
			frappe.throw(_("At once you can either request for paid leaves or unpaid leaves"))
		if (not self.request_for_paid_leave) and (not self.request_for_unpaid_leave):
			frappe.throw(_("Select Request Type"))
		if self.request_for_paid_leave and self.adjust_allow:
			frappe.throw(_("Leave Adjustment only allowed for unpaid leave request"))

	def validate_lwp(self):
		if is_lwp(self.leave_type):
			frappe.throw(_("Leaves cannot be allocated for unpaid leaves"))

	def validate_allocated_leaves(self):
		global allocated
		all = frappe.get_all('Leave Allocation', filters={'employee': self.employee, 'leave_type': self.leave_type}, fields=['name', 'from_date', 'to_date'])
		for a in all:
			c_fd = str(self.from_date)
			d_fd = str(a.from_date)
			c_td = str(self.to_date)
			d_td = str(a.to_date)
			if (c_fd >= d_fd) and (c_td <= d_td):
				allocated = a.name
			elif (c_fd >= d_fd) and not (c_td <= d_td):
				frappe.throw(_("Given dates are overlapping Leave Period"))
			elif (c_td <= d_td) and not (c_fd >= d_fd):
				frappe.throw(_("Given dates are overlapping Leave Period"))
			else:
				frappe.throw(_("No Leave Allocation record found for given dates"))
		return allocated

	def allocate_leaves(self, allocated):
		global additional_leaves
		allo = frappe.get_doc('Leave Allocation', allocated)
		additional_leaves = self.validate_balance_leaves()
		allo.new_leaves_allocated += additional_leaves
		allo.save()

	def generate_leave_application(self, from_date, to_date, leave_type):
		reason = 'Generated in response to Additional Leave Request ' + self.name + '. As requested for ' + self.leave_type + '.'
		new_leave = frappe.get_doc({"doctype":"Leave Application", "employee":self.employee,
		"from_date": from_date, "to_date": to_date, "leave_type": leave_type,
		"status": "Approved", "description": reason, "follow_via_email": False})
		new_leave.insert()
		new_leave.workflow_state = "Approved by Leader"
		new_leave.save()
		new_leave.workflow_state = "Approved"
		new_leave.save()
		new_leave.submit()
		return new_leave.name
		#frappe.msgprint(_("Leave Application {0} has been Approved for this request").format(new_leave.name))

	def validate_balance_leaves(self):
		additional_leaves = 0
		if self.from_date and self.to_date:
			if self.total_leave_days <= 0:
				frappe.throw(_("The day(s) on which you are applying for leave are holidays. You need not apply for leave."))

			if not is_lwp(self.leave_type):
				self.leave_balance = get_leave_balance_on(self.employee, self.leave_type, self.from_date, docname=self.name,
					consider_all_leaves_in_the_allocation_period=True)
				if self.status != "Rejected" and self.leave_balance < self.total_leave_days:
					additional_leaves = self.total_leave_days - self.leave_balance
		if additional_leaves == 0:
			frappe.throw(_("You already have leave balance for requested leaves"))
		return additional_leaves

	def generate_unpaid_leaves(self):
		p_leaves = 0
		string_holidays = []
		number_of_days = date_diff(self.to_date, self.from_date) + 1
		holiday_list = get_holiday_list_for_employee(self.employee)
		holidays = frappe.db.sql("""select holiday_date from `tabHoliday` h1, `tabHoliday List` h2
		where h1.parent = h2.name and h1.holiday_date between %s and %s
		and h2.name = %s""", (self.from_date, self.to_date, holiday_list), as_list=True)
		for h in holidays:
			string_holidays.append(str(h[0]))
		new_to_date = str(self.from_date)
		if number_of_days > 1: #if required leave is only 1 then from date and to date remain same
			 #loop through number of days to check that if a holiday occurs then we will not
			 #include it in leaves so the date will increase every time for loop but it will stop 
			 #the loop when we get required number of leaves for the leave balance
			for i in range(1,number_of_days):
				if new_to_date not in string_holidays:
					p_leaves += 1
				if p_leaves == (self.leave_balance):
					break
				new_to_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
				new_to_date = new_to_date.strftime('%Y-%m-%d')
			new_from_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
			new_from_date = new_from_date.strftime('%Y-%m-%d')
			leave_wop = self.generate_leave_application(new_from_date, self.to_date, 'Leave Without Pay')
		leave_wp = self.generate_leave_application(self.from_date, new_to_date, self.leave_type)
		frappe.msgprint(_("Leave Application {0} and {1} has been Approved for this request").format(leave_wp, leave_wop))

	def generate_adjustable_leaves(self, sick_leave_balance, casual_leave_balance, vacation_leave_balance):
		s_leaves = c_leaves = v_leaves = 0
		p_leaves = 0
		s_holidays = c_holidays = v_holidays = 0
		p_holidays = 0
		new_from_date = False
		leave_name = leave_sick = leave_casual = leave_vacation = leave_wop = ''
		string_holidays = []
		number_of_days = date_diff(self.to_date, self.from_date) + 1
		holiday_list = get_holiday_list_for_employee(self.employee)
		holidays = frappe.db.sql("""select holiday_date from `tabHoliday` h1, `tabHoliday List` h2
		where h1.parent = h2.name and h1.holiday_date between %s and %s
		and h2.name = %s""", (self.from_date, self.to_date, holiday_list), as_list=True)
		for h in holidays:
			string_holidays.append(str(h[0]))
		new_to_date = str(self.from_date)
		if self.leave_balance != 0:
			if number_of_days > 1:
				for i in range(0,number_of_days):
					if new_to_date not in string_holidays:
						p_leaves += 1
					else:
						p_holidays +=1
					if p_leaves == (self.leave_balance):
						break
					new_to_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
					new_to_date = new_to_date.strftime('%Y-%m-%d')
				new_from_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
				new_from_date = new_from_date.strftime('%Y-%m-%d')
				number_of_days = number_of_days - p_leaves - p_holidays
			leave_name = self.generate_leave_application(self.from_date, new_to_date, self.leave_type)
		if number_of_days != 0 and self.leave_type != 'Sick Leave' and sick_leave_balance != 0:
			if new_from_date:
				new_to_date = new_from_date
			else:
				new_from_date = self.from_date
			for i in range(0,number_of_days):
				if new_to_date not in string_holidays:
					s_leaves += 1
				else:
					s_holidays +=1
				if s_leaves == (sick_leave_balance):
					break
				new_to_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
				new_to_date = new_to_date.strftime('%Y-%m-%d')
			leave_sick = self.generate_leave_application(new_from_date, new_to_date, 'Sick Leave')
			new_from_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
			new_from_date = new_from_date.strftime('%Y-%m-%d')
			number_of_days = number_of_days - s_leaves - s_holidays
		if number_of_days != 0 and self.leave_type != 'Casual Leave' and casual_leave_balance != 0:
			if new_from_date:
				new_to_date = new_from_date
			else:
				new_from_date = self.from_date
			for i in range(0,number_of_days):
				if new_to_date not in string_holidays:
					c_leaves += 1
				else:
					c_holidays +=1
				if c_leaves == (casual_leave_balance):
					break
				new_to_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
				new_to_date = new_to_date.strftime('%Y-%m-%d')
			leave_casual = self.generate_leave_application(new_from_date, new_to_date, 'Casual Leave')
			
			new_from_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
			new_from_date = new_from_date.strftime('%Y-%m-%d')
			number_of_days = number_of_days - c_leaves - c_holidays
		if number_of_days != 0 and self.leave_type != 'Vacation Leave' and vacation_leave_balance != 0:
			if new_from_date:
				new_to_date = new_from_date
			else:
				new_from_date = self.from_date
			for i in range(0,number_of_days):
				if new_to_date not in string_holidays:
					v_leaves += 1
				else:
					v_holidays +=1
				if v_leaves == (vacation_leave_balance):
					break
				new_to_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
				new_to_date = new_to_date.strftime('%Y-%m-%d')
			leave_vacation = self.generate_leave_application(new_from_date, new_to_date, 'Vacation Leave')
			new_from_date = datetime.strptime(new_to_date, '%Y-%m-%d') + timedelta(days=1)
			new_from_date = new_from_date.strftime('%Y-%m-%d')
			number_of_days = number_of_days - v_leaves - v_holidays
		if number_of_days != 0:
			if not new_from_date:
				new_from_date = self.from_date
			leave_wop = self.generate_leave_application(new_from_date, self.to_date, 'Leave Without Pay')
		frappe.msgprint(_("Leave Application {0} {1} {2} {3} {4} has been Approved for this request").format(leave_name, leave_sick, leave_casual, leave_vacation, leave_wop))

	def notify_employee(self):
		employee = frappe.get_doc("Employee", self.employee)
		if not employee.user_id:
			return

		parent_doc = frappe.get_doc('Additional Leaves Request', self.name)
		args = parent_doc.as_dict()

		template = frappe.db.get_single_value('HR Settings', 'leave_status_notification_template')
		if not template:
			frappe.msgprint(_("Please set default template for Leave Status Notification in HR Settings."))
			return
		email_template = frappe.get_doc("Email Template", template)
		message = frappe.render_template(email_template.response, args)

		self.notify({
			# for post in messages
			"message": message,
			"message_to": employee.user_id,
			# for email
			"subject": email_template.subject,
			"notify": "employee"
		})

	def notify_leave_approver(self):
		if self.leave_approver:
			parent_doc = frappe.get_doc('Additional Leaves Request', self.name)
			args = parent_doc.as_dict()

			template = frappe.db.get_single_value('HR Settings', 'leave_approval_notification_template')
			if not template:
				frappe.msgprint(_("Please set default template for Leave Approval Notification in HR Settings."))
				return
			email_template = frappe.get_doc("Email Template", template)
			message = frappe.render_template(email_template.response, args)

			self.notify({
				# for post in messages
				"message": message,
				"message_to": self.leave_approver,
				# for email
				"subject": email_template.subject
			})

	def notify(self, args):
		args = frappe._dict(args)
		# args -> message, message_to, subject
		if cint(self.follow_via_email):
			contact = args.message_to
			if not isinstance(contact, list):
				if not args.notify == "employee":
					contact = frappe.get_doc('User', contact).email or contact

			sender      	    = dict()
			sender['email']     = frappe.get_doc('User', frappe.session.user).email
			sender['full_name'] = frappe.utils.get_fullname(sender['email'])

			try:
				frappe.sendmail(
					recipients = contact,
					sender = sender['email'],
					subject = args.subject,
					message = args.message,
				)
				frappe.msgprint(_("Email sent to {0}").format(contact))
			except frappe.OutgoingEmailError:
				pass

@frappe.whitelist()
def get_number_of_leave_days(employee,from_date, to_date):
	number_of_days = 0
	number_of_days = date_diff(to_date, from_date) + 1
	number_of_days = flt(number_of_days) - flt(get_holidays(employee, from_date, to_date))
	return number_of_days