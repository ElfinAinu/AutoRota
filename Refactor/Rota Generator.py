from ortools.sat.python import cp_model
import datetime
import csv
import os
import json
import random

def load_rules(json_filepath):
    with open(json_filepath, "r") as f:
        rules = json.load(f)
    required_rules = rules["Rules"]["required"]
    preferred_rules = rules["Rules"].get("preferred", {})
    return required_rules, preferred_rules

def load_temporary_rules(json_filepath):
    with open(json_filepath, "r") as f:
        temp_rules = json.load(f)
    return temp_rules

day_name_to_index = {
    "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
    "Thursday": 4, "Friday": 5, "Saturday": 6
}

# Domain: 0 = Early, 1 = Middle, 2 = Late, 3 = Day Off (D/O), 4 = Holiday (H)
def initialize_model(num_weeks, days_per_week, employees, shift_to_int):
    model = cp_model.CpModel()
    num_employees = len(employees)
    x = {}
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e in range(num_employees):
                x[w, d, e] = model.NewIntVar(0, 4, f"x[{w},{d},{e}]")
    return model, x

# Enforce allowed shifts per "Will Work" rules.
def add_allowed_shifts(model, required_rules, employees, shift_to_int, x):
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                allowed = set()
                if emp in required_rules.get("Will Work Late", []):
                    allowed.add(shift_to_int["L"])
                if emp in required_rules.get("Will Work Middle", []):
                    allowed.add(shift_to_int["M"])
                if emp in required_rules.get("Will work Early", []):
                    allowed.add(shift_to_int["E"])
                for shift in ["E", "M", "L"]:
                    if shift_to_int[shift] not in allowed:
                        model.Add(x[w, d, e] != shift_to_int[shift])

# For individuals marked as alternating (e.g. in "Every other weekend off"),
# enforce that they get a full weekend off every other week.
def enforce_alternating_weekend_off_required(model, x, days_per_week, num_weeks, employees, shift_to_int, alternating_employees):
    for emp in alternating_employees:
        e = employees.index(emp)
        for w in range(0, num_weeks - 1, 2):
            # Force Saturday (day index 6) of week w and Sunday (day index 0) of week w+1 off.
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"])
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"])

# Daily coverage constraints.
# Each day must have at least one Early and one Late shift filled.
# No more than 3 employees may work on any day.
# Middles are not required; if assigned they count as a working slot.
def add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees):
    num_employees = len(employees)
    for w in range(num_weeks):
        for d in range(days_per_week):
            early_vars = []
            late_vars = []
            working_vars = []
            for e in range(num_employees):
                # Define working as assigned a shift in {E, M, L} (i.e. value <= 2).
                is_working = model.NewBoolVar(f"working_{w}_{d}_{e}")
                model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(is_working)
                model.Add(x[w, d, e] > shift_to_int["L"]).OnlyEnforceIf(is_working.Not())
                working_vars.append(is_working)
                b_early = model.NewBoolVar(f"early_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(b_early)
                model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(b_early.Not())
                early_vars.append(b_early)
                b_late = model.NewBoolVar(f"late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(b_late)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(b_late.Not())
                late_vars.append(b_late)
            # Ensure at least one Early and one Late each day.
            model.Add(sum(early_vars) >= 1)
            model.Add(sum(late_vars) >= 1)
            # Limit total working slots (E, M, or L) to at most 3 per day.
            model.Add(sum(working_vars) <= 3)

# If a non–step-up covers a shift slot, then a step-up need not be used.
def add_stepup_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, stepup_employees):
    num_employees = len(employees)
    non_stepup_indices = [i for i, emp in enumerate(employees) if emp not in stepup_employees]
    stepup_indices = [i for i, emp in enumerate(employees) if emp in stepup_employees]
    for w in range(num_weeks):
        for d in range(days_per_week):
            for shift in ["E", "M", "L"]:
                non_stepup_bools = []
                stepup_bools = []
                for e in range(num_employees):
                    b = model.NewBoolVar(f"cover_{shift}_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int[shift]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int[shift]).OnlyEnforceIf(b.Not())
                    if e in non_stepup_indices:
                        non_stepup_bools.append(b)
                    else:
                        stepup_bools.append(b)
                non_stepup_sum = model.NewIntVar(0, 1, f"nonstep_{shift}_{w}_{d}")
                stepup_sum = model.NewIntVar(0, 1, f"step_{shift}_{w}_{d}")
                model.Add(non_stepup_sum == sum(non_stepup_bools))
                model.Add(stepup_sum == sum(stepup_bools))
                model.Add(stepup_sum <= 1 - non_stepup_sum)

# Enforce employee-specific working days and unavailable days.
def add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x):
    if "Working Days" in required_rules:
        for e, emp in enumerate(employees):
            if emp in required_rules["Working Days"]:
                required_days = required_rules["Working Days"][emp]
                for w in range(num_weeks):
                    work_vars = []
                    for d in range(days_per_week):
                        is_working = model.NewBoolVar(f"emp_working_{emp}_{w}_{d}")
                        model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(is_working)
                        model.Add(x[w, d, e] > shift_to_int["L"]).OnlyEnforceIf(is_working.Not())
                        work_vars.append(is_working)
                    model.Add(sum(work_vars) == required_days)
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            e = employees.index(emp)
            d_idx = day_name_to_index[day]
            for w in range(num_weeks):
                model.Add(x[w, d_idx, e] == shift_to_int["D/O"])

# Enforce temporary rules (holidays, specific day off, or specific shift on a given day) at 100%.
def add_temporary_constraints(model, x, employees, temporary_rules, num_weeks, days_per_week, shift_to_int):
    global_temp = temporary_rules["Required"].get("Everyone", {})
    rota_start_str = global_temp.get("Start Date", "")
    if rota_start_str:
        rota_start = datetime.datetime.strptime(rota_start_str, "%Y/%m/%d")
    else:
        rota_start = datetime.datetime.today()
    for emp in employees:
        if emp in temporary_rules["Required"]:
            emp_rules = temporary_rules["Required"][emp]
            e = employees.index(emp)
            # Specific days off.
            days_off = emp_rules.get("days off", [])
            for day_str in days_off:
                if day_str:
                    off_date = datetime.datetime.strptime(day_str, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if current_date.date() == off_date.date():
                                model.Add(x[w, d, e] == shift_to_int["D/O"])
            # Specific shift requirements.
            for shift_field in ["Early", "Middle", "Late"]:
                req_date_str = emp_rules.get(shift_field, "")
                if req_date_str:
                    req_date = datetime.datetime.strptime(req_date_str, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if current_date.date() == req_date.date():
                                if shift_field == "Early":
                                    model.Add(x[w, d, e] == shift_to_int["E"])
                                elif shift_field == "Middle":
                                    model.Add(x[w, d, e] == shift_to_int["M"])
                                elif shift_field == "Late":
                                    model.Add(x[w, d, e] == shift_to_int["L"])
            # Holiday enforcement.
            holiday = emp_rules.get("holiday", {})
            if holiday.get("active", False):
                start_hol = holiday.get("start", "")
                end_hol = holiday.get("end", "")
                if start_hol and end_hol:
                    hol_start = datetime.datetime.strptime(start_hol, "%Y/%m/%d")
                    hol_end = datetime.datetime.strptime(end_hol, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if hol_start.date() <= current_date.date() <= hol_end.date():
                                model.Add(x[w, d, e] == shift_to_int["H"])

# Objective: reward weekend off days and preferred shift assignments.
def add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alternating_employees):
    objective_terms = []
    # Weekend bonus for non-alternating employees.
    weekend_bonus_full = 10000
    weekend_bonus_partial = 5000
    for w in range(num_weeks):
        for e, emp in enumerate(employees):
            if emp in alternating_employees:
                continue  # Alternating employees are handled separately.
            sat_off = model.NewBoolVar(f"sat_off_{w}_{e}")
            sun_off = model.NewBoolVar(f"sun_off_{w}_{e}")
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"]).OnlyEnforceIf(sat_off)
            model.Add(x[w, days_per_week - 1, e] != shift_to_int["D/O"]).OnlyEnforceIf(sat_off.Not())
            model.Add(x[w, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(sun_off)
            model.Add(x[w, 0, e] != shift_to_int["D/O"]).OnlyEnforceIf(sun_off.Not())
            full_weekend = model.NewBoolVar(f"full_weekend_{w}_{e}")
            model.AddBoolAnd([sat_off, sun_off]).OnlyEnforceIf(full_weekend)
            model.AddBoolOr([sat_off.Not(), sun_off.Not()]).OnlyEnforceIf(full_weekend.Not())
            partial_weekend = model.NewBoolVar(f"partial_weekend_{w}_{e}")
            model.Add(sat_off + sun_off == 1).OnlyEnforceIf(partial_weekend)
            model.Add(sat_off + sun_off != 1).OnlyEnforceIf(partial_weekend.Not())
            objective_terms.append(full_weekend * weekend_bonus_full)
            objective_terms.append(partial_weekend * weekend_bonus_partial)
    # Reward preferred shifts.
    pref_weight = 2000
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                if "Late Shifts" in preferred_rules and emp in preferred_rules["Late Shifts"]:
                    b = model.NewBoolVar(f"pref_late_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(b.Not())
                    objective_terms.append(b * pref_weight)
                if "Early Shifts" in preferred_rules and emp in preferred_rules["Early Shifts"]:
                    b = model.NewBoolVar(f"pref_early_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(b.Not())
                    objective_terms.append(b * pref_weight)
                if "Middle Shifts" in preferred_rules and emp in preferred_rules["Middle Shifts"]:
                    b = model.NewBoolVar(f"pref_mid_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int["M"]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int["M"]).OnlyEnforceIf(b.Not())
                    objective_terms.append(b * pref_weight)
    model.Maximize(sum(objective_terms))

# ---- Main script starts here ----

num_weeks = 4
days_per_week = 7
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load rules.
rules_filepath = os.path.join(script_dir, "Rules.json")
required_rules, preferred_rules = load
