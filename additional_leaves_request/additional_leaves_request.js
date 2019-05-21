// Copyright (c) 2019, Mujadidia Inc and contributors
// For license information, please see license.txt

cur_frm.add_fetch('employee','employee_name','employee_name');
cur_frm.add_fetch('employee','company','company');

// cur_frm.set_value('leave_type', 'Paid Leave')

frappe.ui.form.on("Additional Leaves Request", {
	setup: function(frm) {
		frm.set_query("leave_approver", function() {
			return {
				query: "erpnext.hr.doctype.department_approver.department_approver.get_approvers",
				filters: {
					employee: frm.doc.employee,
					doctype: frm.doc.doctype
				}
			};
		});

		frm.set_query("employee", erpnext.queries.employee);
	},
	onload: function(frm) {
		if (!frm.doc.posting_date) {
			frm.set_value("posting_date", frappe.datetime.get_today());
		}
		if (frm.doc.docstatus == 0) {
			return frappe.call({
				method: "mujadidia_hr.mujadidia_hr.doctype.leave_application.leave_application.get_mandatory_approval",
				args: {
					doctype: frm.doc.doctype,
				},
				callback: function(r) {
					if (!r.exc && r.message) {
						frm.toggle_reqd("leave_approver", true);
					}
				}
			});
		}
		frm.trigger("make_dashboard");
	},

	make_dashboard: function(frm) {
		var leave_details;
		var leave_totals;
		if (frm.doc.employee) {
			frappe.call({
				method: "mujadidia_hr.mujadidia_hr.doctype.leave_application.leave_application.get_leave_details",
				async: false,
				args: {
					employee: frm.doc.employee,
					date: frm.doc.posting_date
				},
				callback: function(r) {
					if (!r.exc && r.message['leave_allocation']) {
						leave_details = r.message['leave_allocation'];
						console.log(leave_details);
						console.log('here2');
					}
					if (!r.exc && r.message['total_leave_allocation']) {
						leave_totals = r.message['total_leave_allocation'];
						console.log(leave_totals);
						console.log('here');
					}
					if (!r.exc && r.message['leave_approver']) {
						frm.set_value('leave_approver', r.message['leave_approver']);
					}
				}
			});

			$("div").remove(".form-dashboard-section");
			let section = frm.dashboard.add_section(
				frappe.render_template('additional_leaves_request_dashboard', {
					data: leave_details,
					total_data: leave_totals
				})
			);
			frm.dashboard.show();
		}
	},

	refresh: function(frm) {
		// if (frm.is_new()) {
		// 	frm.trigger("calculate_total_days");
		// }
		cur_frm.set_intro("");
		if(frm.doc.__islocal && !in_list(frappe.user_roles, "Employee")) {
			frm.set_intro(__("Fill the form and save it"));
		}

		if (!frm.doc.employee && frappe.defaults.get_user_permissions()) {
			const perm = frappe.defaults.get_user_permissions();
			if (perm && perm['Employee']) {
				frm.set_value('employee', perm['Employee'].map(perm_doc => perm_doc.doc)[0]);
			}
		}
		frm.trigger("make_dashboard");
	},

	employee: function(frm) {
		// frappe.call({
		// 	method: "mujadidia_hr.mujadidia_hr.doctype.leave_application.leave_application.set_leave_type",
		// 	args: {
		// 		employee: frm.doc.employee,
		// 		date: frm.doc.posting_date
		// 	},
		// 	callback: function(r) {
		// 		if (!r.exc && r.message) {
		// 			frm.set_value("leave_type", r.message);
		// 		}
		// 	}
		// });
		frm.trigger("make_dashboard");
		// frm.trigger("get_leave_balance");
		frm.trigger("set_leave_approver");
	},

	from_date_sick: function(frm) {
		if(frm.doc.from_date_sick){
			cur_frm.set_df_property("to_date_sick", "reqd", 1);
		}
		if(!frm.doc.from_date_sick){
			cur_frm.set_df_property("to_date_sick", "reqd", 0);
		}
	},

	from_date_casual: function(frm) {
		if(frm.doc.from_date_sick){
			cur_frm.set_df_property("to_date_casual", "reqd", 1);
		}
		if(!frm.doc.from_date_sick){
			cur_frm.set_df_property("to_date_casual", "reqd", 0);
		}
	},

	from_date_vacation: function(frm) {
		if(frm.doc.from_date_vacation){
			cur_frm.set_df_property("to_date_vacation", "reqd", 1);
		}
		if(!frm.doc.from_date_vacation){
			cur_frm.set_df_property("to_date_vacation", "reqd", 0);
		}
	},

	from_date: function(frm) {
		frm.trigger("calculate_total_days");
	},

	to_date: function(frm) {
		frm.trigger("calculate_total_days");
	},

	leave_type: function(frm) {
		frm.trigger("get_leave_balance");
	},

	leave_approver: function(frm) {
		if(frm.doc.leave_approver){
			frm.set_value("leave_approver_name", frappe.user.full_name(frm.doc.leave_approver));
		}
	},

	get_leave_balance: function(frm) {
		if(frm.doc.docstatus==0 && frm.doc.employee && frm.doc.leave_type && frm.doc.from_date) {
			return frappe.call({
				method: "mujadidia_hr.mujadidia_hr.doctype.leave_application.leave_application.get_leave_balance_on",
				args: {
					employee: frm.doc.employee,
					date: frm.doc.from_date,
					leave_type: frm.doc.leave_type,
					consider_all_leaves_in_the_allocation_period: true
				},
				callback: function(r) {
					if (!r.exc && r.message) {
						frm.set_value('leave_balance', r.message);
					}
					else {
						frm.set_value('leave_balance', "0");
					}
				}
			});
		}
	},

	calculate_total_days: function(frm) {
		if(frm.doc.from_date && frm.doc.to_date && frm.doc.employee) {

			var from_date = Date.parse(frm.doc.from_date);
			var to_date = Date.parse(frm.doc.to_date);

			if(to_date < from_date){
				frappe.msgprint(__("To Date cannot be less than From Date"));
				frm.set_value('to_date', '');
				return;
			}
				// server call is done to include holidays in leave days calculations
			return frappe.call({
				method: 'mujadidia_hr.mujadidia_hr.doctype.additional_leaves_request.additional_leaves_request.get_number_of_leave_days',
				args: {
					"employee": frm.doc.employee,
					"from_date": frm.doc.from_date,
					"to_date": frm.doc.to_date,
					// "half_day": frm.doc.half_day,
					// "half_day_date": frm.doc.half_day_date,
				},
				callback: function(r) {
					if (r && r.message) {
						frm.set_value('total_leave_days', r.message);
					}
				}
			});
		}
	},

	set_leave_approver: function(frm) {
		if(frm.doc.employee) {
				// server call is done to include holidays in leave days calculations
			return frappe.call({
				method: 'mujadidia_hr.mujadidia_hr.doctype.leave_application.leave_application.get_leave_approver',
				args: {
					"employee": frm.doc.employee,
				},
				callback: function(r) {
					if (r && r.message) {
						frm.set_value('leave_approver', r.message);
					}
				}
			});
		}
	}
});

