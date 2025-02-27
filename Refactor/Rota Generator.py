import os
import re
import datetime
import csv
import json
from ortools.sat.python import cp_model

# ------------------------------------------------------
# 1) LOAD RULES & TEMPORARY RULES
# ------------------------------------------------------
def load_rules(json_filepath):
    with open(json_filepath, "r") as f:
        rules = json.load(f)
    required = rules["Rules"]["required"]
    preferred = rules["Rules"].get("preferred", {})
    return required, preferred

def load_temporary_rules(json_filepath):
    with open(json_filepath, "r") as f:
        temp = json.load(f)
    return temp

day_name_to_index = {
    "Sunday": 0, "Monday": 1, "Tuesday": 2,
    "Wednesday": 3, "Thursday": 4, "Friday": 5,
    "Saturday": 6
}

shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3, "H": 4}
int_to_shift = {v: k for k, v in shift_to_int.items()}

# ------------------------------------------------------
# 2) FIND PREVIOUS ROTA CSV (IF NEEDED)
# ------------------------------------------------------
def find_latest_csv(output_dir):
    pattern = r"Rota - (\d{4})-(\d{2})-(\d{2})\.csv"
    latest_date = None
    latest_file = None
    if not os.path.exists(output_dir):
        return None
    for fname in os.listdir(output_dir):
        match = re.match(pattern, fname)
        if match:
            y, m, d = match.groups()
            file_date = datetime.date(int(y), int(m), int(d))
            if latest_date is None or file_date > latest_date:
                latest_date = file_date
                latest_file = os.path.join(output_dir, fname)
    return latest_file

# ------------------------------------------------------
# 3) MODEL INITIALIZATION
# ------------------------------------------------------
def initialize_model(num_weeks, days_per_week, employees):
    model = cp_model.CpModel()
    x = {}
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                # Each assignment is in {0=E,1=M,2=L,3=D/O,4=H}
                x[w, d, e] = model.NewIntVar(0, 4, f"x[{w},{d},{e}]")
    return model, x

# ------------------------------------------------------
# 4) REQUIRED CONSTRAINTS
# ------------------------------------------------------

# (a) Shift leaders = exactly N days; step-ups = at most N days
def add_weekly_work_constraints(model, x, num_weeks, days_per_week, employees, required_work, shift_leaders, stepup_employees):
    for w in range(num_weeks):
        for e, emp in enumerate(employees):
            # A "work" day is when x is in {E, M, L}
            daily_bools = []
            for d in range(days_per_week):
                b = model.NewBoolVar(f"work_{w}_{d}_{e}")
                model.Add(x[w, d, e] <= 2).OnlyEnforceIf(b)
                model.Add(x[w, d, e] >= 3).OnlyEnforceIf(b.Not())
                daily_bools.append(b)
            if emp in shift_leaders:
                must_work = required_work.get(emp, 5)
                model.Add(sum(daily_bools) == must_work)
            elif emp in stepup_employees:
                max_work = required_work.get(emp, 2)
                model.Add(sum(daily_bools) <= max_work)

# (b) Allowed shifts (hard): If employee is working (E/M/L), it must be one they are “Will Work …”
def add_allowed_shifts_required(model, x, num_weeks, days_per_week, employees, required_rules):
    for e, emp in enumerate(employees):
        # Build set of allowed working shifts
        allowed_work_shifts = set()
        if "Will Work Late" in required_rules and emp in required_rules["Will Work Late"]:
            allowed_work_shifts.add(shift_to_int["L"])
        if "Will Work Middle" in required_rules and emp in required_rules["Will Work Middle"]:
            allowed_work_shifts.add(shift_to_int["M"])
        if "Will work Early" in required_rules and emp in required_rules["Will work Early"]:
            allowed_work_shifts.add(shift_to_int["E"])
        # The employee can also have D/O (3) or H (4), so combine them
        allowed_shifts = list(allowed_work_shifts) + [shift_to_int["D/O"], shift_to_int["H"]]
        # Create a set of 1-tuples for AddAllowedAssignments
        allowed_assignments = [(val,) for val in allowed_shifts]
        for w in range(num_weeks):
            for d in range(days_per_week):
                model.AddAllowedAssignments([x[w, d, e]], allowed_assignments)

# (c) Alternating weekend: for each pair of consecutive weeks, the employee must have exactly one weekend fully off
def add_alternating_weekend_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees, alt_emps):
    # For each alt employee, in each pair of consecutive weeks w, w+1:
    # - one weekend (Sat,Sun) is off, the other is not fully off.
    # We'll define "weekend_off_w" as "both Sunday=0 and Saturday=6 are D/O"
    for emp in alt_emps:
        e = employees.index(emp)
        for w in range(0, num_weeks - 1, 2):
            # For week w
            off_sun_w = model.NewBoolVar(f"off_sun_{w}_{e}")
            off_sat_w = model.NewBoolVar(f"off_sat_{w}_{e}")
            weekend_off_w = model.NewBoolVar(f"weekend_off_{w}_{e}")
            model.Add(x[w, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(off_sun_w)
            model.Add(x[w, 0, e] != shift_to_int["D/O"]).OnlyEnforceIf(off_sun_w.Not())
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"]).OnlyEnforceIf(off_sat_w)
            model.Add(x[w, days_per_week - 1, e] != shift_to_int["D/O"]).OnlyEnforceIf(off_sat_w.Not())
            model.AddBoolAnd([off_sun_w, off_sat_w]).OnlyEnforceIf(weekend_off_w)
            model.AddBoolOr([off_sun_w.Not(), off_sat_w.Not()]).OnlyEnforceIf(weekend_off_w.Not())
            # For week w+1
            off_sun_wp = model.NewBoolVar(f"off_sun_{w+1}_{e}")
            off_sat_wp = model.NewBoolVar(f"off_sat_{w+1}_{e}")
            weekend_off_wp = model.NewBoolVar(f"weekend_off_{w+1}_{e}")
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(off_sun_wp)
            model.Add(x[w+1, 0, e] != shift_to_int["D/O"]).OnlyEnforceIf(off_sun_wp.Not())
            model.Add(x[w+1, days_per_week - 1, e] == shift_to_int["D/O"]).OnlyEnforceIf(off_sat_wp)
            model.Add(x[w+1, days_per_week - 1, e] != shift_to_int["D/O"]).OnlyEnforceIf(off_sat_wp.Not())
            model.AddBoolAnd([off_sun_wp, off_sat_wp]).OnlyEnforceIf(weekend_off_wp)
            model.AddBoolOr([off_sun_wp.Not(), off_sat_wp.Not()]).OnlyEnforceIf(weekend_off_wp.Not())
            # Exactly one weekend is fully off
            model.Add(weekend_off_w + weekend_off_wp == 1)

# (d) Temporary constraints: requested days off, holiday intervals, or forced shifts
def add_temporary_constraints(model, x, num_weeks, days_per_week, employees, temp_rules):
    global_temp = temp_rules["Required"].get("Everyone", {})
    rota_start_str = global_temp.get("Start Date", "")
    if rota_start_str:
        rota_start = datetime.datetime.strptime(rota_start_str, "%Y/%m/%d")
    else:
        rota_start = datetime.datetime.today()
    for e, emp in enumerate(employees):
        if emp in temp_rules["Required"]:
            rules = temp_rules["Required"][emp]
            # Days off
            for day_str in rules.get("days off", []):
                if day_str:
                    off_date = datetime.datetime.strptime(day_str, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if current_date.date() == off_date.date():
                                model.Add(x[w, d, e] == shift_to_int["D/O"])
            # Specific shift requests
            for shift_field in ["Early", "Middle", "Late"]:
                req_date_str = rules.get(shift_field, "")
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
            # Holiday
            holiday = rules.get("holiday", {})
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

# (e) Daily coverage: at least one Early and one Late every day
def add_daily_coverage_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees):
    for w in range(num_weeks):
        for d in range(days_per_week):
            early_bools = []
            late_bools = []
            for e, emp in enumerate(employees):
                be = model.NewBoolVar(f"early_{w}_{d}_{e}")
                bl = model.NewBoolVar(f"late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(be)
                model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(be.Not())
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(bl)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(bl.Not())
                early_bools.append(be)
                late_bools.append(bl)
            model.Add(sum(early_bools) >= 1)
            model.Add(sum(late_bools) >= 1)

# ------------------------------------------------------
# 5) OBJECTIVE: PREFERENCE & STEP-UP MINIMIZATION
# ------------------------------------------------------
def add_objective(model, x, num_weeks, days_per_week, employees, preferred_rules, stepup_employees):
    objective_terms = []
    # Reward for each preferred shift
    pref_bonus = 1000
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                if "Late Shifts" in preferred_rules and emp in preferred_rules["Late Shifts"]:
                    b = model.NewBoolVar(f"prefL_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(b.Not())
                    objective_terms.append(b * pref_bonus)
                if "Early Shifts" in preferred_rules and emp in preferred_rules["Early Shifts"]:
                    b = model.NewBoolVar(f"prefE_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(b.Not())
                    objective_terms.append(b * pref_bonus)
                if "Middle Shifts" in preferred_rules and emp in preferred_rules["Middle Shifts"]:
                    b = model.NewBoolVar(f"prefM_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int["M"]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int["M"]).OnlyEnforceIf(b.Not())
                    objective_terms.append(b * pref_bonus)
                # If "Days" is used to indicate specific day-of-week preference:
                if "Days" in preferred_rules and emp in preferred_rules["Days"]:
                    # Suppose the user lists day names, e.g. "Friday", "Sunday"
                    # We can do something approximate: figure out the day name from d
                    # but that requires a reference date or just map d to day_name_to_index in reverse
                    # For simplicity, we do day_name = {0:'Sunday',1:'Monday',...}[d]
                    # This only works if each w is an identical repeated pattern
                    # If that matches a preference, add partial bonus if they're working that day
                    # (the user can adjust as needed)
                    day_index_to_name = {0:'Sunday',1:'Monday',2:'Tuesday',3:'Wednesday',4:'Thursday',5:'Friday',6:'Saturday'}
                    day_name = day_index_to_name[d]
                    if day_name in preferred_rules["Days"][emp]:
                        b2 = model.NewBoolVar(f"prefDay_{w}_{d}_{e}")
                        model.Add(x[w, d, e] <= 2).OnlyEnforceIf(b2)  # working shift
                        model.Add(x[w, d, e] >= 3).OnlyEnforceIf(b2.Not())
                        # smaller bonus
                        objective_terms.append(b2 * (pref_bonus // 2))

    # Penalize step-up usage (use them only when needed)
    stepup_penalty = 500
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                if emp in stepup_employees:
                    used = model.NewBoolVar(f"stepup_used_{w}_{d}_{e}")
                    model.Add(x[w, d, e] <= 2).OnlyEnforceIf(used)
                    model.Add(x[w, d, e] >= 3).OnlyEnforceIf(used.Not())
                    objective_terms.append(-stepup_penalty * used)

    model.Maximize(sum(objective_terms))

# ------------------------------------------------------
# 6) OUTPUT
# ------------------------------------------------------
def build_schedule(solver, x, num_weeks, days_per_week, employees):
    schedule = {}
    for w in range(num_weeks):
        schedule[w] = {}
        for d in range(days_per_week):
            schedule[w][d] = {}
            for e, emp in enumerate(employees):
                shift_val = solver.Value(x[w, d, e])
                schedule[w][d][emp] = int_to_shift[shift_val]
    return schedule

def write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, stepup_employees):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for w in range(num_weeks):
            week_start = start_date + datetime.timedelta(days=w*days_per_week)
            header = ["Name"] + [(week_start + datetime.timedelta(days=d)).strftime("%a %d/%m") for d in range(days_per_week)]
            writer.writerow(header)
            for e, emp in enumerate(employees):
                row = [emp]
                for d in range(days_per_week):
                    shift_str = schedule[w][d][emp]
                    # For step-ups, if day off, leave blank
                    if emp in stepup_employees and shift_str == "D/O":
                        row.append("")
                    else:
                        row.append(shift_str)
                writer.writerow(row)
            writer.writerow([])

# ------------------------------------------------------
# 7) MAIN
# ------------------------------------------------------
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "output")
    
    # Load JSON rules
    rules_filepath = os.path.join(script_dir, "Rules.json")
    required_rules, preferred_rules = load_rules(rules_filepath)
    with open(rules_filepath, "r") as f:
        rules_data = json.load(f)
    shift_leaders = rules_data.get("employees-shift_leaders", [])
    stepup_employees = rules_data.get("employees-step_up", [])
    employees = shift_leaders + [emp for emp in stepup_employees if emp not in shift_leaders]
    
    # Identify the “Every other weekend off” employees
    alt_emps = required_rules.get("Every other weekend off", [])
    
    # Possibly read an existing CSV if you want continuity
    latest_csv = find_latest_csv(output_dir)
    
    # Basic settings
    num_weeks = 4
    days_per_week = 7
    
    model, x = initialize_model(num_weeks, days_per_week, employees)
    
    # Required constraints
    required_work = required_rules.get("Working Days", {})
    add_weekly_work_constraints(model, x, num_weeks, days_per_week, employees, required_work, shift_leaders, stepup_employees)
    add_allowed_shifts_required(model, x, num_weeks, days_per_week, employees, required_rules)
    if alt_emps:
        add_alternating_weekend_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees, alt_emps)
    
    temp_rules = load_temporary_rules(os.path.join(script_dir, "Temporary Rules.json"))
    add_temporary_constraints(model, x, num_weeks, days_per_week, employees, temp_rules)
    
    # Daily coverage: at least one Early + one Late
    add_daily_coverage_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees)
    
    # Objective: preferences + minimal step-up usage
    add_objective(model, x, num_weeks, days_per_week, employees, preferred_rules, stepup_employees)
    
    # Solve
    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    
    # Figure out the start date from Temporary Rules or use now
    global_temp = temp_rules["Required"].get("Everyone", {})
    if "Start Date" in global_temp and global_temp["Start Date"]:
        start_date = datetime.datetime.strptime(global_temp["Start Date"], "%Y/%m/%d")
        out_date_str = start_date.strftime("%Y-%m-%d")
    else:
        start_date = datetime.datetime.today()
        out_date_str = start_date.strftime("%Y-%m-%d")
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = build_schedule(solver, x, num_weeks, days_per_week, employees)
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"Rota - {out_date_str}.csv")
        write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, stepup_employees)
        print("Solution found. Wrote to:", os.path.abspath(output_file))
    else:
        print("No solution found.")

if __name__ == "__main__":
    main()
