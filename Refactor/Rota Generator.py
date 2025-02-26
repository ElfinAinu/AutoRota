from ortools.sat.python import cp_model
import datetime
import csv
import os
import json
import random

# --- Loading rules from JSON files ---

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

# Mapping day names to indices.
day_name_to_index = {
    "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
    "Thursday": 4, "Friday": 5, "Saturday": 6
}

# --- Model Initialization ---
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

# --- Allowed Shift Constraints ---
# For each day, if an employee is not listed as eligible for a given working shift (Early, Middle, Late)
# in the "Will Work ..." lists from required_rules, then that shift is forbidden.
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

# --- Alternating Weekend Off ---
# For employees designated in the "Every other weekend off" list, enforce that in every alternate pair of weeks,
# they get a full weekend off (Saturday of week w and Sunday of week w+1).
def enforce_alternating_weekend_off_required(model, x, days_per_week, num_weeks, employees, shift_to_int, alternating_employees):
    for emp in alternating_employees:
        e = employees.index(emp)
        for w in range(0, num_weeks - 1, 2):
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"])  # Saturday off in week w.
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"])                # Sunday off in week w+1.

# --- Daily Coverage Constraints ---
# Each day must have at least one Early and one Late assigned.
# Middles are not required. In addition, the total number of working slots (Early, Middle, or Late) is capped at 3.
def add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees):
    num_employees = len(employees)
    for w in range(num_weeks):
        for d in range(days_per_week):
            early_vars = []
            late_vars = []
            working_vars = []
            for e in range(num_employees):
                # Working if assigned any of Early, Middle, or Late (values 0, 1, or 2).
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
            model.Add(sum(early_vars) >= 1)
            model.Add(sum(late_vars) >= 1)
            model.Add(sum(working_vars) <= 3)

# --- Step-Up Priority Constraint ---
# For each shift slot, if a non–step‑up employee (shift leader) is assigned, then no step‑up is used for that slot.
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

# --- Employee-Specific Constraints ---
# Enforce the "Working Days" requirement and unavailable days ("Days won't work") from required_rules.
def add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week):
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

# --- Temporary Constraints ---
# Enforce any temporary requirements (holidays, specific day off, or specific shift on a given day)
# with absolute priority.
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

# --- Objective Function ---
# Reward weekend off patterns and preferred shift assignments.
def add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alternating_employees):
    objective_terms = []
    # Weekend bonus for non-alternating employees.
    weekend_bonus_full = 10000
    weekend_bonus_partial = 5000
    for w in range(num_weeks):
        for e, emp in enumerate(employees):
            # Only non-alternating employees get weekend bonus in the objective.
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
            # Enforce partial if exactly one day off.
            model.Add(sat_off + sun_off == 1).OnlyEnforceIf(partial_weekend)
            model.Add(sat_off + sun_off != 1).OnlyEnforceIf(partial_weekend.Not())
            objective_terms.append(full_weekend * weekend_bonus_full)
            objective_terms.append(partial_weekend * weekend_bonus_partial)
    # Reward preferred shift assignments.
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

# --- Main Script Execution ---

num_weeks = 4
days_per_week = 7
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load required and preferred rules.
rules_filepath = os.path.join(script_dir, "Rules.json")
required_rules, preferred_rules = load_rules(rules_filepath)
with open(rules_filepath, "r") as f:
    rules_data = json.load(f)
shift_leaders = rules_data.get("employees-shift_leaders", [])
stepup_employees = rules_data.get("employees-step_up", [])
employees = shift_leaders + [emp for emp in stepup_employees if emp not in shift_leaders]

# Extract alternating employees (for every other weekend off).
alternating_employees = []
if "Every other weekend off" in required_rules:
    alternating_employees = required_rules["Every other weekend off"]

shifts = ["E", "M", "L", "D/O", "H"]
shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3, "H": 4}
int_to_shift = {0: "E", 1: "M", 2: "L", 3: "D/O", 4: "H"}

# Initialize the model.
model, x = initialize_model(num_weeks, days_per_week, employees, shift_to_int)

# Enforce allowed shifts per "Will Work" rules.
add_allowed_shifts(model, required_rules, employees, shift_to_int, x, num_weeks, days_per_week)

# Enforce daily coverage constraints.
add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees)

# Enforce step-up priority.
add_stepup_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, stepup_employees)

# Enforce employee-specific constraints from required_rules.
add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week)

# Enforce temporary constraints (holidays, specific days off, or specific shifts).
temp_filepath = os.path.join(script_dir, "Temporary Rules.json")
temporary_rules = load_temporary_rules(temp_filepath)
add_temporary_constraints(model, x, employees, temporary_rules, num_weeks, days_per_week, shift_to_int)

# Enforce alternating weekend off for designated employees.
if alternating_employees:
    enforce_alternating_weekend_off_required(model, x, days_per_week, num_weeks, employees, shift_to_int, alternating_employees)

# Add the objective function.
add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alternating_employees)

# Solve the model.
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

def write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees):
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
                    row.append(schedule[w][d][emp])
                writer.writerow(row)
            writer.writerow([])

# Get start date from temporary rules.
global_temp = temporary_rules["Required"].get("Everyone", {})
if "Start Date" in global_temp:
    start_date = datetime.datetime.strptime(global_temp["Start Date"], "%Y/%m/%d")
else:
    start_date = datetime.datetime.today()

if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    schedule = build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift)
    output_file = os.path.join(script_dir, "output", "rota.csv")
    write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees)
    print("Solution found. Wrote to:", os.path.abspath(output_file))
else:
    print("No solution found.")
