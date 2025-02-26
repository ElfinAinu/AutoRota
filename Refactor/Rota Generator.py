from ortools.sat.python import cp_model
import datetime
from ortools.sat import cp_model_pb2
import random
import csv
import os
import json

def load_rules(json_filepath):
    with open(json_filepath, "r") as f:
        refactored_rules = json.load(f)
    required_rules = refactored_rules["Rules"]["required"]
    preferred_rules = refactored_rules["Rules"].get("preferred", {})
    return required_rules, preferred_rules

def load_temporary_rules(json_filepath):
    with open(json_filepath, "r") as f:
        temporary_rules = json.load(f)
    return temporary_rules

day_name_to_index = {
    "Sunday": 0,
    "Monday": 1,
    "Tuesday": 2,
    "Wednesday": 3,
    "Thursday": 4,
    "Friday": 5,
    "Saturday": 6
}

def initialize_model(num_weeks, days_per_week, employees, shift_to_int):
    global slack_work_days, days_wont_work_vars  # Declare global variables
    slack_work_days = []
    days_wont_work_vars = []
    model = cp_model.CpModel()
    num_employees = len(employees)
    x = {}
    work = {}
    slack_work_days = []
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e in range(num_employees):
                x[w, d, e] = model.NewIntVar(0, 4, f"x[{w},{d},{e}]")
                work[w, d, e] = model.NewBoolVar(f"work[{w},{d},{e}]")
                model.Add(x[w, d, e] != shift_to_int["D/O"]).OnlyEnforceIf(work[w, d, e])
                model.Add(x[w, d, e] == shift_to_int["D/O"]).OnlyEnforceIf(work[w, d, e].Not())
    
    total_days = num_weeks * days_per_week
    global_work = {}
    def day_index(w, d):
        return w * days_per_week + d
    for w in range(num_weeks):
        for d in range(days_per_week):
            i = day_index(w, d)
            for e in range(num_employees):
                global_work[i, e] = model.NewBoolVar(f"global_work[{i},{e}]")
                model.Add(global_work[i, e] == 1).OnlyEnforceIf(work[w, d, e])
                model.Add(global_work[i, e] == 0).OnlyEnforceIf(work[w, d, e].Not())
    return model, x, work, global_work, total_days

def calc_duplicate_shift_leader_penalty(model, x, shift_to_int, num_weeks, days_per_week, shift_leaders, duplicate_penalty_factor):
    duplicate_penalty_expr = 0
    for w in range(num_weeks):
        for d in range(days_per_week):
            for shift in ["E", "M", "L"]:
                indicators = []
                for i in range(len(shift_leaders)):  # assume shift leaders are at the start of employees list
                    ind = model.NewBoolVar(f"dup_indicator_{shift}_w{w}_d{d}_{i}")
                    model.Add(x[w, d, i] == shift_to_int[shift]).OnlyEnforceIf(ind)
                    model.Add(x[w, d, i] != shift_to_int[shift]).OnlyEnforceIf(ind.Not())
                    indicators.append(ind)
                dup_aux = model.NewIntVar(0, len(shift_leaders) - 1, f"dup_aux_{shift}_w{w}_d{d}")
                model.Add(dup_aux + 1 >= sum(indicators))
                model.Add(dup_aux >= sum(indicators) - 1)
                duplicate_penalty_expr += duplicate_penalty_factor * dup_aux
    return duplicate_penalty_expr

num_weeks = 4
days_per_week = 7
script_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(script_dir, "Rules.json"), "r") as f:
    rules_data = json.load(f)
shift_leaders = rules_data.get("employees-shift_leaders", [])
stepup_employees = rules_data.get("employees-step_up", [])
employees = shift_leaders + [emp for emp in stepup_employees if emp not in shift_leaders]
shifts = ["E", "M", "L", "D/O", "H"]
shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3, "H": 4}
int_to_shift = {0: "E", 1: "M", 2: "L", 3: "D/O", 4: "H"}
model, x, work, global_work, total_days = initialize_model(num_weeks, days_per_week, employees, shift_to_int)

###############################################################################
# Add Daily Coverage Constraint: at least one Early and one Late each day.
###############################################################################
def add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, num_employees):
    for w in range(num_weeks):
        for d in range(days_per_week):
            early_bools = []
            late_bools = []
            for e in range(num_employees):
                is_early = model.NewBoolVar(f"is_early_{w}_{d}_{e}")
                is_late  = model.NewBoolVar(f"is_late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(is_early)
                model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(is_early.Not())
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(is_late)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(is_late.Not())
                early_bools.append(is_early)
                late_bools.append(is_late)
            model.Add(sum(early_bools) >= 1)
            model.Add(sum(late_bools) >= 1)

add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, len(employees))

###############################################################################
# Add Week Boundary Constraint: No Late-to-Early across week boundaries.
###############################################################################
def add_week_boundary_constraints(model, x, shift_to_int, num_weeks, employees):
    num_employees = len(employees)
    for e in range(num_employees):
        for w in range(num_weeks - 1):
            was_late = model.NewBoolVar(f"sat_late_{w}_{e}")
            model.Add(x[w, 6, e] == shift_to_int["L"]).OnlyEnforceIf(was_late)
            model.Add(x[w, 6, e] != shift_to_int["L"]).OnlyEnforceIf(was_late.Not())
            model.Add(x[w+1, 0, e] != shift_to_int["E"]).OnlyEnforceIf(was_late)

add_week_boundary_constraints(model, x, shift_to_int, num_weeks, employees)

###############################################################################
# NEW: Add Weekly Work Constraints to enforce days off according to Rules.
###############################################################################
def add_weekly_work_constraints(model, work, num_weeks, days_per_week, employees, stepup_employees):
    global slack_work_days
    for w in range(num_weeks):
        for e in range(len(employees)):
            day_work = [work[w, d, e] for d in range(days_per_week)]
            if employees[e] in stepup_employees:
                model.Add(sum(day_work) <= 3)
            else:
                slack_work_days_var = model.NewIntVar(0, 1, f"slack_work_days_{w}_{e}")
                model.Add(sum(day_work) >= 4 - slack_work_days_var)
                model.Add(sum(day_work) <= 6 + slack_work_days_var)
                slack_work_days.append(slack_work_days_var)

def add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, work, num_weeks, days_per_week):
    if "Working Days" in required_rules:
        for e, emp in enumerate(employees):
            if emp in required_rules["Working Days"]:
                required_days = required_rules["Working Days"][emp]
                for w in range(num_weeks):
                    if emp in stepup_employees:
                        model.Add(sum(work[w, d, e] for d in range(days_per_week)) <= required_days)
                    else:
                        model.Add(sum(work[w, d, e] for d in range(days_per_week)) == required_days)
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            e = employees.index(emp)
            day_idx = day_name_to_index[day]
            for w in range(num_weeks):
                compliance = model.NewBoolVar(f"compliance_{emp}_{w}_{day}")
                model.Add(x[w, day_idx, e] == shift_to_int["D/O"]).OnlyEnforceIf(compliance)
                model.Add(x[w, day_idx, e] != shift_to_int["D/O"]).OnlyEnforceIf(compliance.Not())
                days_wont_work_vars.append(compliance)

###############################################################################
# NEW: Add Temporary Constraints (if any)
###############################################################################
def add_temporary_constraints(model, x, employees, temporary_rules, num_weeks, days_per_week, shift_to_int):
    global_rules = temporary_rules["Required"].get("Everyone", {})
    rota_start_str = global_rules.get("Start Date", "")
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
                    hol_start_date = datetime.datetime.strptime(start_hol, "%Y/%m/%d")
                    hol_end_date = datetime.datetime.strptime(end_hol, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if hol_start_date.date() <= current_date.date() <= hol_end_date.date():
                                model.Add(x[w, d, e] == shift_to_int["H"])

###############################################################################
# (Other functions, e.g. add_weekend_off_constraints and objective functions, are defined below.)
###############################################################################
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

def write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, stepup_employees):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, mode="w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for w in range(num_weeks):
            week_start = start_date + datetime.timedelta(days=w * days_per_week)
            header = ["Name"] + [f"{(week_start + datetime.timedelta(days=d)).strftime('%a')} - {(week_start + datetime.timedelta(days=d)).strftime('%d/%m')}" for d in range(days_per_week)]
            writer.writerow(header)
            for e, emp in enumerate(employees):
                row = [emp]
                for d in range(days_per_week):
                    shift_str = schedule[w][d][emp]
                    if emp in stepup_employees and shift_str == "D/O":
                        row.append("")
                    else:
                        row.append(shift_str)
                writer.writerow(row)
            writer.writerow([])

###############################################################################
# Insert new constraint calls into the model before solving.
###############################################################################
# Load required and preferred rules.
rules_filepath = os.path.join(script_dir, "Rules.json")
required_rules, preferred_rules = load_rules(rules_filepath)

# Add weekly work constraints and employee-specific required rules.
add_weekly_work_constraints(model, work, num_weeks, days_per_week, employees, stepup_employees)
add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, work, num_weeks, days_per_week)

# Load and add any temporary constraints.
temporary_rules = load_temporary_rules(os.path.join(script_dir, "Temporary Rules.json"))
add_temporary_constraints(model, x, employees, temporary_rules, num_weeks, days_per_week, shift_to_int)

###############################################################################
# Solve the model.
###############################################################################
solver = cp_model.CpSolver()
solver.parameters.random_seed = int(datetime.datetime.now().timestamp())
status = solver.Solve(model)

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    temp_file = os.path.join(script_dir, "Temporary Rules.json")
    temporary_rules = load_temporary_rules(temp_file)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift)
        global_temp = temporary_rules["Required"].get("Everyone", {})
        if "Start Date" in global_temp:
            start_date = datetime.datetime.strptime(global_temp["Start Date"], "%Y/%m/%d")
        else:
            start_date = datetime.datetime.strptime("23/02/2025", "%d/%m/%Y")
        output_file = os.path.join(script_dir, "output", "rota.csv")
        write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, stepup_employees)
        print("Solution found. Wrote to:", os.path.abspath(output_file))
    else:
        print("No solution found.")

if __name__ == "__main__":
    main()
