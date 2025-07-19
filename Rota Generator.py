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

def load_last_rota(output_dir):
    import glob, re
    rota_files = glob.glob(os.path.join(output_dir, "Rota - *.csv"))
    if not rota_files:
        return {}
    def extract_date(filename):
        m = re.search(r"Rota - (\d{4}-\d{2}-\d{2})\.csv", filename)
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%d") if m else datetime.datetime.min
    rota_files.sort(key=extract_date)
    last_file = rota_files[-1]
    blocks = []
    with open(last_file, "r", newline="") as csvfile:
        reader = csv.reader(csvfile)
        current = []
        for row in reader:
            if not any(cell.strip() for cell in row):
                if current:
                    blocks.append(current)
                    current = []
            else:
                current.append(row)
        if current:
            blocks.append(current)
    if not blocks:
        return {}
    last_block = blocks[-1]
    if len(last_block) < 2:
        return {}
    previous_state = {}
    header = last_block[0]
    for row in last_block[1:]:
        emp = row[0]
        # Compute consecutive working days by scanning from the end of the week.
        consec = 0
        for shift in reversed(row[1:]):
            if shift.strip() not in ["D/O", "", "H"]:
                consec += 1
            else:
                break
        sun_shift = row[1].strip() if len(row) > 1 else ""
        sat_shift = row[-1].strip() if len(row) >= 7 else ""
        weekend_off = (sun_shift == "D/O" and sat_shift == "D/O")
        previous_state[emp] = {"consecutive": consec,
                               "weekend_off": weekend_off}
    return previous_state

day_name_to_index = {
    "Sunday": 0,
    "Monday": 1,
    "Tuesday": 2,
    "Wednesday": 3,
    "Thursday": 4,
    "Friday": 5,
    "Saturday": 6
}

# Domain: 0 = Early, 1 = Middle, 2 = Late, 3 = Day Off (D/O), 4 = Holiday (H)
def initialize_model(num_weeks, days_per_week, employees, shift_to_int):
    model = cp_model.CpModel()
    x = {}
    num_employees = len(employees)
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e in range(num_employees):
                x[w, d, e] = model.NewIntVar(0, 4, f"x[{w},{d},{e}]")
    return model, x

def add_allowed_shifts(model, required_rules, employees, shift_to_int, x, num_weeks, days_per_week):
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

def enforce_strict_alternating_weekends(model, x, shift_to_int, num_weeks, days_per_week, employees, alt_emps, weekend_offsets):
    for emp in alt_emps:
        e = employees.index(emp)
        offset = weekend_offsets.get(emp, 0)
        for w in range(num_weeks - 1):
            if (w + offset) % 2 == 0:
                # Enforce weekend off for this adjusted week
                model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"])  # Saturday
                model.Add(x[w + 1, 0, e] == shift_to_int["D/O"])               # Sunday of next week
            else:
                model.Add(x[w, days_per_week - 1, e] != shift_to_int["D/O"])
                model.Add(x[w + 1, 0, e] != shift_to_int["D/O"])

def add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees):
    num_employees = len(employees)
    for w in range(num_weeks):
        for d in range(days_per_week):
            early_vars = []
            late_vars = []
            working_vars = []
            for e in range(num_employees):
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
                
                # No Middle on weekends
                if d == 0 or d == days_per_week - 1:
                    model.Add(x[w, d, e] != shift_to_int["M"])
            model.Add(sum(early_vars) >= 1)
            model.Add(sum(late_vars) >= 1)
            model.Add(sum(working_vars) <= 4)

def add_reserve_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, reserve_employees):
    num_employees = len(employees)
    # Identify indices for employees who are NOT reserves (the duty managers)
    non_reserve_indices = [i for i, emp in enumerate(employees) if emp not in reserve_employees]
    
    for w in range(num_weeks):
        for d in range(days_per_week):
            for shift in ["E", "M", "L"]:
                non_reserve_bools = []
                reserve_bools = []
                for e in range(num_employees):
                    b = model.NewBoolVar(f"cover_{shift}_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int[shift]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int[shift]).OnlyEnforceIf(b.Not())
                    if e in non_reserve_indices:
                        non_reserve_bools.append(b)
                    else:
                        reserve_bools.append(b)
                        
                # Set up sum variables over booleans with appropriate domains.
                non_reserve_sum = model.NewIntVar(0, len(non_reserve_bools), f"nonres_{shift}_{w}_{d}")
                reserve_sum = model.NewIntVar(0, len(reserve_bools), f"res_{shift}_{w}_{d}")
                model.Add(non_reserve_sum == sum(non_reserve_bools))
                model.Add(reserve_sum == sum(reserve_bools))
                
                # Create a helper Boolean that is true if any non-reserve (duty manager) is assigned.
                non_reserve_present = model.NewBoolVar(f"non_reserve_present_{shift}_{w}_{d}")
                model.Add(non_reserve_sum >= 1).OnlyEnforceIf(non_reserve_present)
                model.Add(non_reserve_sum == 0).OnlyEnforceIf(non_reserve_present.Not())
                
                # Enforce that if any duty manager is covering the shift, then no reserve is allowed.
                model.Add(reserve_sum == 0).OnlyEnforceIf(non_reserve_present)

def add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week, duty_managers, reserve_employees):
    # Enforce "Working Days" exactly for duty managers and at most for reserves.
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
                    if emp in duty_managers:
                        model.Add(sum(work_vars) == required_days)
                    elif emp in reserve_employees:
                        model.Add(sum(work_vars) <= required_days)
                    else:
                        model.Add(sum(work_vars) == required_days)
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            if emp in employees:
                e = employees.index(emp)
                d_idx = day_name_to_index[day]
                for w in range(num_weeks):
                    model.Add(x[w, d_idx, e] == shift_to_int["D/O"])

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
            days_off = emp_rules.get("days off", [])
            for day_str in days_off:
                if day_str:
                    off_date = datetime.datetime.strptime(day_str, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if current_date.date() == off_date.date():
                                model.Add(x[w, d, e] == shift_to_int["D/O"])
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

def add_no_late_to_early_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees):
    num_employees = len(employees)
    for w in range(num_weeks):
        for d in range(days_per_week - 1):
            for e in range(num_employees):
                was_late = model.NewBoolVar(f"late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(was_late)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(was_late.Not())
                model.Add(x[w, d+1, e] != shift_to_int["E"]).OnlyEnforceIf(was_late)
    for w in range(num_weeks - 1):
        for e in range(num_employees):
            was_late = model.NewBoolVar(f"late_{w}_{days_per_week-1}_{e}")
            model.Add(x[w, days_per_week-1, e] == shift_to_int["L"]).OnlyEnforceIf(was_late)
            model.Add(x[w, days_per_week-1, e] != shift_to_int["L"]).OnlyEnforceIf(was_late.Not())
            model.Add(x[w+1, 0, e] != shift_to_int["E"]).OnlyEnforceIf(was_late)

def add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alternating_employees):
    objective_terms = []
    weekend_bonus_full = 5000
    weekend_bonus_partial = 2500
    for w in range(num_weeks):
        for e, emp in enumerate(employees):
            if emp in alternating_employees:
                continue
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

num_weeks = 4
days_per_week = 7
script_dir = os.path.dirname(os.path.abspath(__file__))

rules_filepath = os.path.join(script_dir, "Rules.json")
required_rules, preferred_rules = load_rules(rules_filepath)
with open(rules_filepath, "r") as f:
    rules_data = json.load(f)
duty_managers = rules_data.get("employees-duty_manager", [])
reserve_employees = rules_data.get("employees-duty_manager-reserve", [])
employees = duty_managers + [emp for emp in reserve_employees if emp not in duty_managers]

alternating_employees = []
if "Every other weekend off" in required_rules:
    alternating_employees = required_rules["Every other weekend off"]

# Define the mapping between shift names and integer values.
shift_to_int = {
    "E": 0,    # Early
    "M": 1,    # Middle
    "L": 2,    # Late
    "D/O": 3,  # Day Off
    "H": 4     # Holiday
}

# Inverse mapping: useful for converting the solver’s output to shift names.
int_to_shift = {v: k for k, v in shift_to_int.items()}

def add_consecutive_working_constraints(model, x, shift_to_int, employees, num_weeks, days_per_week, previous_state):
    total_days = num_weeks * days_per_week
    work = {}
    consec = {}
    for e, emp in enumerate(employees):
        init = previous_state.get(emp, {}).get("consecutive", 0)
        for t in range(total_days):
            w = t // days_per_week
            d = t % days_per_week
            work[(e, t)] = model.NewBoolVar(f"work_{emp}_{t}")
            model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(work[(e, t)])
            model.Add(x[w, d, e] >  shift_to_int["L"]).OnlyEnforceIf(work[(e, t)].Not())
            consec[(e, t)] = model.NewIntVar(0, 6, f"consec_{emp}_{t}")
            if t == 0:
                model.Add(consec[(e, 0)] == init + 1).OnlyEnforceIf(work[(e, 0)])
                model.Add(consec[(e, 0)] == 0).OnlyEnforceIf(work[(e, 0)].Not())
            else:
                model.Add(consec[(e, t)] == consec[(e, t-1)] + 1).OnlyEnforceIf(work[(e, t)])
                model.Add(consec[(e, t)] == 0).OnlyEnforceIf(work[(e, t)].Not())
            model.Add(consec[(e, t)] <= 6)

model, x = initialize_model(num_weeks, days_per_week, employees, shift_to_int)
output_dir = os.path.join(script_dir, "output")
previous_state = load_last_rota(output_dir)
for e, emp in enumerate(employees):
    if previous_state.get(emp, {}).get("consecutive", 0) >= 6:
        model.Add(x[0, 0, e] == shift_to_int["D/O"])

add_allowed_shifts(model, required_rules, employees, shift_to_int, x, num_weeks, days_per_week)
add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees)
add_reserve_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, reserve_employees)
# Enforce required working days exactly (no slack) for duty managers and at most for reserves.
add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week, duty_managers, reserve_employees)
temp_filepath = os.path.join(script_dir, "Temporary Rules.json")
temporary_rules = load_temporary_rules(temp_filepath)
add_temporary_constraints(model, x, employees, temporary_rules, num_weeks, days_per_week, shift_to_int)
weekend_offsets = {}
for emp in alternating_employees:
    weekend_offsets[emp] = 1 if previous_state.get(emp, {}).get("weekend_off", False) else 0
if alternating_employees:
    enforce_strict_alternating_weekends(model, x, shift_to_int, num_weeks, days_per_week, employees, alternating_employees, weekend_offsets)
add_no_late_to_early_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees)
add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alternating_employees)
add_consecutive_working_constraints(model, x, shift_to_int, employees, num_weeks, days_per_week, previous_state)

solver = cp_model.CpSolver()
solver.parameters.random_seed = int(datetime.datetime.now().timestamp())
status = solver.Solve(model)

def build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift):
    schedule = {}
    for w in range(num_weeks):
        schedule[w] = {}
        for d in range(days_per_week):
            schedule[w][d] = {}
            for e, emp in enumerate(employees):
                val = solver.Value(x[w, d, e])
                schedule[w][d][emp] = int_to_shift[val]
    return schedule

def write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, reserve_employees):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, mode="w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for w in range(num_weeks):
            week_start = start_date + datetime.timedelta(days=w * days_per_week)
            header = ["Name"] + [f"{(week_start + datetime.timedelta(days=d)).strftime('%a %d/%m')}" for d in range(days_per_week)]
            writer.writerow(header)
            for e, emp in enumerate(employees):
                row = [emp]
                for d in range(days_per_week):
                    shift_str = schedule[w][d][emp]
                    if emp in reserve_employees and shift_str == "D/O":
                        row.append("")
                    else:
                        row.append(shift_str)
                writer.writerow(row)
            writer.writerow([])

global_temp = temporary_rules["Required"].get("Everyone", {})
if "Start Date" in global_temp:
    start_date = datetime.datetime.strptime(global_temp["Start Date"], "%Y/%m/%d")
    out_date_str = start_date.strftime("%Y-%m-%d")
else:
    start_date = datetime.datetime.today()
    out_date_str = start_date.strftime("%Y-%m-%d")

if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    schedule = build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift)
    output_file = os.path.join(script_dir, "output", f"Rota - {out_date_str}.csv")
    write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, reserve_employees)
    print("Solution found. Wrote to:", os.path.abspath(output_file))
else:
    print("No solution found.")
