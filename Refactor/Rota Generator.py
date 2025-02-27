import os
import re
import datetime
import csv
import json
import random
from ortools.sat.python import cp_model

# -----------------------------------------------------------------------------
# 1) LOAD RULES & TEMPORARY RULES
# -----------------------------------------------------------------------------
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

# SHIFT encoding: 0 = Early, 1 = Middle, 2 = Late, 3 = Day Off ("D/O"), 4 = Holiday ("H")
shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3, "H": 4}
int_to_shift = {v: k for k, v in shift_to_int.items()}

# -----------------------------------------------------------------------------
# 2) READ PREVIOUS ROTA CSV (for continuity)
# -----------------------------------------------------------------------------
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

def parse_csv_blocks(csv_path):
    if not csv_path or not os.path.exists(csv_path):
        return []
    lines = []
    with open(csv_path, "r", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if any(cell.strip() for cell in row):
                lines.append(row)
            else:
                lines.append([])
    blocks = []
    current = []
    for row in lines:
        if row:
            current.append(row)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    return blocks

def parse_last_week_consecutive_days(csv_path, employees):
    blocks = parse_csv_blocks(csv_path)
    if not blocks:
        return {emp: 0 for emp in employees}
    last_block = blocks[-1]
    if not last_block:
        return {emp: 0 for emp in employees}
    employee_data = {}
    for row in last_block[1:]:
        if row:
            emp_name = row[0]
            shifts = row[1:8]  # Expect 7 days (Sun..Sat)
            employee_data[emp_name] = shifts
    results = {}
    for emp in employees:
        shifts = employee_data.get(emp, [])
        if not shifts:
            results[emp] = 0
            continue
        work_flags = [False if (s.strip()=="" or s.strip().upper()=="D/O") else True for s in shifts]
        consec = 0
        for flag in reversed(work_flags):
            if flag:
                consec += 1
            else:
                break
        results[emp] = consec
    return results

def parse_last_week_alternating(csv_path, employees):
    blocks = parse_csv_blocks(csv_path)
    if not blocks:
        return {}
    last_block = blocks[-1]
    if not last_block or len(last_block) < 2:
        return {}
    header = last_block[0]
    if len(header) < 8:
        return {}
    alt_data = {}
    for row in last_block[1:]:
        if not row:
            continue
        emp_name = row[0]
        if len(row) < 8:
            continue
        sat = row[7].strip()
        sun = row[1].strip()
        def is_off(s): return s=="" or s.upper()=="D/O"
        alt_data[emp_name] = "off" if (is_off(sat) and is_off(sun)) else "on"
    return alt_data

# -----------------------------------------------------------------------------
# 3) MODEL INITIALIZATION & WORKING VARIABLES
# -----------------------------------------------------------------------------
def initialize_model(num_weeks, days_per_week, employees):
    model = cp_model.CpModel()
    x = {}
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                x[w, d, e] = model.NewIntVar(0, 4, f"x[{w},{d},{e}]")
    return model, x

def add_working_vars(model, x, num_weeks, days_per_week, employees):
    work = {}
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                work[w, d, e] = model.NewBoolVar(f"work_{w}_{d}_{e}")
                model.Add(x[w, d, e] <= 2).OnlyEnforceIf(work[w, d, e])
                model.Add(x[w, d, e] >= 3).OnlyEnforceIf(work[w, d, e].Not())
    return work

# -----------------------------------------------------------------------------
# 4) CONSTRAINTS
# -----------------------------------------------------------------------------
def add_weekly_work_constraints(model, x, work, num_weeks, days_per_week, employees, required_work, stepup_employees):
    slack_vars = []
    for w in range(num_weeks):
        for e, emp in enumerate(employees):
            if emp in required_work:
                req = required_work[emp]
                weekly = []
                for d in range(days_per_week):
                    b = model.NewBoolVar(f"weekly_work_{emp}_{w}_{d}")
                    model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] >= 3).OnlyEnforceIf(b.Not())
                    weekly.append(b)
                if emp in stepup_employees:
                    model.Add(sum(weekly) <= req)
                else:
                    slack = model.NewIntVar(0, days_per_week, f"slack_{emp}_{w}")
                    slack_vars.append(slack)
                    model.Add(sum(weekly) + slack == req)
    return slack_vars

def add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees):
    for w in range(num_weeks):
        for d in range(days_per_week):
            early = []
            late = []
            for e, emp in enumerate(employees):
                b_early = model.NewBoolVar(f"daily_early_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(b_early)
                model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(b_early.Not())
                early.append(b_early)
                b_late = model.NewBoolVar(f"daily_late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(b_late)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(b_late.Not())
                late.append(b_late)
                if d==0 or d==days_per_week-1:
                    model.Add(x[w, d, e] != shift_to_int["M"])
            model.Add(sum(early) >= 1)
            model.Add(sum(late) >= 1)

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

def add_days_wont_work(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week):
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            if emp in employees:
                e = employees.index(emp)
                d_idx = day_name_to_index[day]
                for w in range(num_weeks):
                    model.Add(x[w, d_idx, e] == shift_to_int["D/O"])

def add_no_late_to_early_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees):
    for w in range(num_weeks):
        for d in range(days_per_week-1):
            for e, emp in enumerate(employees):
                flag = model.NewBoolVar(f"late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(flag)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(flag.Not())
                model.Add(x[w, d+1, e] != shift_to_int["E"]).OnlyEnforceIf(flag)
    for w in range(num_weeks-1):
        for e, emp in enumerate(employees):
            flag = model.NewBoolVar(f"late_bound_{w}_{e}")
            model.Add(x[w, days_per_week-1, e] == shift_to_int["L"]).OnlyEnforceIf(flag)
            model.Add(x[w, days_per_week-1, e] != shift_to_int["L"]).OnlyEnforceIf(flag.Not())
            model.Add(x[w+1, 0, e] != shift_to_int["E"]).OnlyEnforceIf(flag)

def add_stepup_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, stepup_employees):
    non_stepup = [i for i, emp in enumerate(employees) if emp not in stepup_employees]
    for w in range(num_weeks):
        for d in range(days_per_week):
            for shift in ["E", "M", "L"]:
                nonstep = []
                step = []
                for e, emp in enumerate(employees):
                    b = model.NewBoolVar(f"{shift}_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int[shift]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int[shift]).OnlyEnforceIf(b.Not())
                    if e in non_stepup:
                        nonstep.append(b)
                    else:
                        step.append(b)
                sum_nonstep = model.NewIntVar(0, 1, f"sum_nonstep_{w}_{d}_{shift}")
                sum_step = model.NewIntVar(0, 1, f"sum_step_{w}_{d}_{shift}")
                model.Add(sum_nonstep == sum(nonstep))
                model.Add(sum_step == sum(step))
                model.Add(sum_step <= 1 - sum_nonstep)

def add_alternating_weekends(model, x, shift_to_int, num_weeks, days_per_week, employees, alt_emps):
    for emp in alt_emps:
        e = employees.index(emp)
        for w in range(0, num_weeks-1, 2):
            model.Add(x[w, days_per_week-1, e] == shift_to_int["D/O"])
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"])

def add_temporary_constraints(model, x, employees, temp_rules, num_weeks, days_per_week, shift_to_int):
    global_temp = temp_rules["Required"].get("Everyone", {})
    rota_start_str = global_temp.get("Start Date", "")
    if rota_start_str:
        rota_start = datetime.datetime.strptime(rota_start_str, "%Y/%m/%d")
    else:
        rota_start = datetime.datetime.today()
    for emp in employees:
        if emp in temp_rules["Required"]:
            rules = temp_rules["Required"][emp]
            e = employees.index(emp)
            for day_str in rules.get("days off", []):
                if day_str:
                    off_date = datetime.datetime.strptime(day_str, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week+d)
                            if current_date.date() == off_date.date():
                                model.Add(x[w, d, e] == shift_to_int["D/O"])
            for shift_field in ["Early", "Middle", "Late"]:
                req_date_str = rules.get(shift_field, "")
                if req_date_str:
                    req_date = datetime.datetime.strptime(req_date_str, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week+d)
                            if current_date.date() == req_date.date():
                                if shift_field == "Early":
                                    model.Add(x[w, d, e] == shift_to_int["E"])
                                elif shift_field == "Middle":
                                    model.Add(x[w, d, e] == shift_to_int["M"])
                                elif shift_field == "Late":
                                    model.Add(x[w, d, e] == shift_to_int["L"])
            holiday = rules.get("holiday", {})
            if holiday.get("active", False):
                start_hol = holiday.get("start", "")
                end_hol = holiday.get("end", "")
                if start_hol and end_hol:
                    hol_start = datetime.datetime.strptime(start_hol, "%Y/%m/%d")
                    hol_end = datetime.datetime.strptime(end_hol, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week+d)
                            if hol_start.date() <= current_date.date() <= hol_end.date():
                                model.Add(x[w, d, e] == shift_to_int["H"])

def add_consecutive_day_limit(model, work, num_weeks, days_per_week, employees, max_consecutive=6):
    total_days = num_weeks * days_per_week
    for e, emp in enumerate(employees):
        for start in range(total_days - 6):
            block = []
            for offset in range(7):
                w = (start + offset) // days_per_week
                d = (start + offset) % days_per_week
                block.append(work[w, d, e])
            model.Add(sum(block) <= max_consecutive)

# -----------------------------------------------------------------------------
# 7) ADD EMPLOYEE-SPECIFIC CONSTRAINTS
# -----------------------------------------------------------------------------
def add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week, stepup_employees):
    working_slacks = []
    if "Working Days" in required_rules:
        for w in range(num_weeks):
            for e, emp in enumerate(employees):
                if emp in required_rules["Working Days"]:
                    req = required_rules["Working Days"][emp]
                    work_vars = []
                    for d in range(days_per_week):
                        b = model.NewBoolVar(f"spec_work_{emp}_{w}_{d}")
                        model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(b)
                        model.Add(x[w, d, e] >= 3).OnlyEnforceIf(b.Not())
                        work_vars.append(b)
                    if emp in stepup_employees:
                        model.Add(sum(work_vars) <= req)
                    else:
                        slack = model.NewIntVar(0, days_per_week, f"spec_slack_{emp}_{w}")
                        working_slacks.append(slack)
                        model.Add(sum(work_vars) + slack == req)
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            if emp in employees:
                e = employees.index(emp)
                d_idx = day_name_to_index[day]
                for w in range(num_weeks):
                    model.Add(x[w, d_idx, e] == shift_to_int["D/O"])
    return working_slacks

# -----------------------------------------------------------------------------
# 8) OBJECTIVE FUNCTION
# -----------------------------------------------------------------------------
def add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alt_emps, working_slacks, late_to_early_penalties):
    objective_terms = []
    weekend_bonus_full = 5000
    weekend_bonus_partial = 500
    for w in range(num_weeks):
        for e, emp in enumerate(employees):
            if emp in alt_emps:
                continue
            sat_off = model.NewBoolVar(f"sat_off_{w}_{e}")
            sun_off = model.NewBoolVar(f"sun_off_{w}_{e}")
            model.Add(x[w, days_per_week-1, e] == shift_to_int["D/O"]).OnlyEnforceIf(sat_off)
            model.Add(x[w, days_per_week-1, e] != shift_to_int["D/O"]).OnlyEnforceIf(sat_off.Not())
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
    SLACK_WEIGHT = 50000
    if working_slacks:
        objective_terms.append(-SLACK_WEIGHT * sum(working_slacks))
    model.Maximize(sum(objective_terms))

# -----------------------------------------------------------------------------
# 9) SCHEDULE OUTPUT FUNCTIONS
# -----------------------------------------------------------------------------
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
    with open(output_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for w in range(num_weeks):
            week_start = start_date + datetime.timedelta(days=w*days_per_week)
            header = ["Name"] + [f"{(week_start+datetime.timedelta(days=d)).strftime('%a %d/%m')}" for d in range(days_per_week)]
            writer.writerow(header)
            for e, emp in enumerate(employees):
                row = [emp]
                for d in range(days_per_week):
                    shift_str = schedule[w][d][emp]
                    if emp in stepup_employees and shift_str=="D/O":
                        row.append("")
                    else:
                        row.append(shift_str)
                writer.writerow(row)
            writer.writerow([])

# -----------------------------------------------------------------------------
# 10) MAIN EXECUTION
# -----------------------------------------------------------------------------
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "output")
    
    # Load rules.
    rules_filepath = os.path.join(script_dir, "Rules.json")
    required_rules, preferred_rules = load_rules(rules_filepath)
    with open(rules_filepath, "r") as f:
        rules_data = json.load(f)
    shift_leaders = rules_data.get("employees-shift_leaders", [])
    stepup_employees = rules_data.get("employees-step_up", [])
    employees = shift_leaders + [emp for emp in stepup_employees if emp not in shift_leaders]
    
    # Retrieve previous rota data.
    latest_csv = find_latest_csv(output_dir)
    prev_consecutive_dict = parse_last_week_consecutive_days(latest_csv, employees)
    alt_last_weekend_dict = parse_last_week_alternating(latest_csv, employees)
    
    num_weeks = 4
    days_per_week = 7
    
    model, x = initialize_model(num_weeks, days_per_week, employees)
    work = add_working_vars(model, x, num_weeks, days_per_week, employees)
    
    required_work = required_rules.get("Working Days", {})
    working_slacks = add_weekly_work_constraints(model, x, work, num_weeks, days_per_week, employees, required_work, stepup_employees)
    
    add_allowed_shifts(model, required_rules, employees, shift_to_int, x, num_weeks, days_per_week)
    add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees)
    add_stepup_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, stepup_employees)
    add_days_wont_work(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week)
    add_no_late_to_early_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees)
    add_consecutive_day_limit(model, work, num_weeks, days_per_week, employees, max_consecutive=6)
    working_slacks += add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week, stepup_employees)
    
    temp_filepath = os.path.join(script_dir, "Temporary Rules.json")
    temp_rules = load_temporary_rules(temp_filepath)
    add_temporary_constraints(model, x, employees, temp_rules, num_weeks, days_per_week, shift_to_int)
    
    alt_employees = required_rules.get("Every other weekend off", [])
    if alt_employees:
        for emp in alt_employees:
            e = employees.index(emp)
            last_state = alt_last_weekend_dict.get(emp, None)
            if last_state == "off":
                model.Add(x[0, days_per_week-1, e] != shift_to_int["D/O"])
                if num_weeks > 1:
                    model.Add(x[1, 0, e] != shift_to_int["D/O"])
            elif last_state == "on":
                model.Add(x[0, days_per_week-1, e] == shift_to_int["D/O"])
                if num_weeks > 1:
                    model.Add(x[1, 0, e] == shift_to_int["D/O"])
        add_alternating_weekends(model, x, shift_to_int, num_weeks, days_per_week, employees, alt_employees)
    
    # Hard Late-to-Early constraint remains; no penalty variables needed.
    late_to_early_penalties = []
    
    add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alt_employees, working_slacks, late_to_early_penalties)
    
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = int(datetime.datetime.now().timestamp())
    status = solver.Solve(model)
    
    global_temp = temp_rules["Required"].get("Everyone", {})
    if "Start Date" in global_temp:
        start_date = datetime.datetime.strptime(global_temp["Start Date"], "%Y/%m/%d")
        out_date_str = start_date.strftime("%Y-%m-%d")
    else:
        start_date = datetime.datetime.today()
        out_date_str = start_date.strftime("%Y-%m-%d")
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift)
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"Rota - {out_date_str}.csv")
        write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, stepup_employees)
        print("Solution found. Wrote to:", os.path.abspath(output_file))
    else:
        print("No solution found.")

if __name__ == "__main__":
    main()
