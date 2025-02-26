import os
import re
import datetime
import csv
import json
import random
from ortools.sat.python import cp_model

# --------------------------------------------------------------------------------
# 1) LOADING RULES
# --------------------------------------------------------------------------------
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
    "Sunday": 0,
    "Monday": 1,
    "Tuesday": 2,
    "Wednesday": 3,
    "Thursday": 4,
    "Friday": 5,
    "Saturday": 6
}

# SHIFT ENCODING:
# 0 = Early, 1 = Middle, 2 = Late, 3 = Day Off (D/O), 4 = Holiday (H)

# --------------------------------------------------------------------------------
# 2) READING THE MOST RECENT CSV
# --------------------------------------------------------------------------------
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
            if any(row):
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
    header = last_block[0] if last_block else []
    employee_data = {}
    for row in last_block[1:]:
        if row:
            emp_name = row[0]
            shifts_7 = row[1:8]
            employee_data[emp_name] = shifts_7
    results = {}
    for emp in employees:
        shifts_7 = employee_data.get(emp, [])
        if not shifts_7:
            results[emp] = 0
            continue
        is_working = []
        for s in shifts_7:
            s = s.strip()
            if s == "" or s.upper() == "D/O":
                is_working.append(False)
            else:
                is_working.append(True)
        c = 0
        for val in reversed(is_working):
            if val:
                c += 1
            else:
                break
        results[emp] = c
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
    sunday_col = 1
    saturday_col = 7
    alt_data = {}
    for row in last_block[1:]:
        if not row:
            continue
        emp_name = row[0]
        if len(row) < 8:
            continue
        sat_str = row[saturday_col].strip()
        sun_str = row[sunday_col].strip()
        def is_off(s):
            return s == "" or s.upper() == "D/O"
        both_off = is_off(sat_str) and is_off(sun_str)
        if both_off:
            alt_data[emp_name] = "off"
        else:
            alt_data[emp_name] = "on"
    return alt_data

# --------------------------------------------------------------------------------
# 3) MODEL & CONSTRAINTS
# --------------------------------------------------------------------------------
def initialize_model(num_weeks, days_per_week, employees):
    model = cp_model.CpModel()
    x = {}
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                x[w, d, e] = model.NewIntVar(0, 4, f"x[{w},{d},{e}]")
    return model, x

def add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees):
    for w in range(num_weeks):
        for d in range(days_per_week):
            early_bools = []
            late_bools = []
            working_bools = []
            for e, emp in enumerate(employees):
                is_work = model.NewBoolVar(f"working_{w}_{d}_{e}")
                model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(is_work)
                model.Add(x[w, d, e] > shift_to_int["L"]).OnlyEnforceIf(is_work.Not())
                working_bools.append(is_work)
                b_early = model.NewBoolVar(f"early_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(b_early)
                model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(b_early.Not())
                early_bools.append(b_early)
                b_late = model.NewBoolVar(f"late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(b_late)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(b_late.Not())
                late_bools.append(b_late)
                if d == 0 or d == days_per_week - 1:
                    model.Add(x[w, d, e] != shift_to_int["M"])
            model.Add(sum(early_bools) >= 1)
            model.Add(sum(late_bools) >= 1)
            model.Add(sum(working_bools) <= 4)

def add_no_late_to_early_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees):
    for w in range(num_weeks):
        for d in range(days_per_week - 1):
            for e, emp in enumerate(employees):
                was_late = model.NewBoolVar(f"late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(was_late)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(was_late.Not())
                model.Add(x[w, d+1, e] != shift_to_int["E"]).OnlyEnforceIf(was_late)
    for w in range(num_weeks - 1):
        for e, emp in enumerate(employees):
            was_late = model.NewBoolVar(f"late_boundary_{w}_{e}")
            model.Add(x[w, days_per_week-1, e] == shift_to_int["L"]).OnlyEnforceIf(was_late)
            model.Add(x[w, days_per_week-1, e] != shift_to_int["L"]).OnlyEnforceIf(was_late.Not())
            model.Add(x[w+1, 0, e] != shift_to_int["E"]).OnlyEnforceIf(was_late)

def add_stepup_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, stepup_employees):
    non_stepup = [i for i, emp in enumerate(employees) if emp not in stepup_employees]
    for w in range(num_weeks):
        for d in range(days_per_week):
            for shift in ["E", "M", "L"]:
                nonstep_bools = []
                step_bools = []
                for e, emp in enumerate(employees):
                    b = model.NewBoolVar(f"{shift}_{w}_{d}_{e}")
                    model.Add(x[w, d, e] == shift_to_int[shift]).OnlyEnforceIf(b)
                    model.Add(x[w, d, e] != shift_to_int[shift]).OnlyEnforceIf(b.Not())
                    if e in non_stepup:
                        nonstep_bools.append(b)
                    else:
                        step_bools.append(b)
                sum_nonstep = model.NewIntVar(0, 1, f"sum_nonstep_{w}_{d}_{shift}")
                sum_step = model.NewIntVar(0, 1, f"sum_step_{w}_{d}_{shift}")
                model.Add(sum_nonstep == sum(nonstep_bools))
                model.Add(sum_step == sum(step_bools))
                model.Add(sum_step <= 1 - sum_nonstep)

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

def add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week):
    working_slacks = []
    if "Working Days" in required_rules:
        for e, emp in enumerate(employees):
            if emp in required_rules["Working Days"]:
                required_days = required_rules["Working Days"][emp]
                for w in range(num_weeks):
                    w_bools = []
                    for d in range(days_per_week):
                        is_work = model.NewBoolVar(f"work_{emp}_{w}_{d}")
                        model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(is_work)
                        model.Add(x[w, d, e] > shift_to_int["L"]).OnlyEnforceIf(is_work.Not())
                        w_bools.append(is_work)
                    slack = model.NewIntVar(0, days_per_week, f"slack_{emp}_{w}")
                    model.Add(sum(w_bools) + slack == required_days)
                    working_slacks.append(slack)
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            if emp in employees:
                e = employees.index(emp)
                d_idx = day_name_to_index[day]
                for w in range(num_weeks):
                    model.Add(x[w, d_idx, e] == shift_to_int["D/O"])
    return working_slacks

def add_temporary_constraints(model, x, employees, temp_rules, num_weeks, days_per_week, shift_to_int):
    global_temp = temp_rules["Required"].get("Everyone", {})
    rota_start_str = global_temp.get("Start Date", "")
    if rota_start_str:
        rota_start = datetime.datetime.strptime(rota_start_str, "%Y/%m/%d")
    else:
        rota_start = datetime.datetime.today()
    for emp in employees:
        if emp in temp_rules["Required"]:
            emp_rules = temp_rules["Required"][emp]
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

def enforce_strict_alternating_weekends(model, x, shift_to_int, num_weeks, days_per_week, employees, alt_emps):
    for emp in alt_emps:
        e = employees.index(emp)
        for w in range(num_weeks - 1):
            if w % 2 == 0:
                model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"])
                model.Add(x[w + 1, 0, e] == shift_to_int["D/O"])
            else:
                model.Add(x[w, days_per_week - 1, e] != shift_to_int["D/O"])
                model.Add(x[w + 1, 0, e] != shift_to_int["D/O"])

def add_consecutive_day_limit_with_offset(model, x, shift_to_int, num_weeks, days_per_week, employees, prev_consecutive, max_consecutive=6):
    total_days = num_weeks * days_per_week
    is_work = {}
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                b = model.NewBoolVar(f"working_global_{w}_{d}_{e}")
                model.Add(x[w, d, e] <= shift_to_int["L"]).OnlyEnforceIf(b)
                model.Add(x[w, d, e] > shift_to_int["L"]).OnlyEnforceIf(b.Not())
                is_work[w, d, e] = b
    def day_index(wi, di):
        return wi * days_per_week + di
    for e, emp in enumerate(employees):
        c = prev_consecutive.get(emp, 0)
        for start_day in range(total_days - (max_consecutive - 1)):
            seg = []
            for offset in range(max_consecutive):
                d_ind = start_day + offset
                w_ind = d_ind // days_per_week
                dd = d_ind % days_per_week
                seg.append(is_work[w_ind, dd, e])
            model.Add(sum(seg) + c <= max_consecutive)

def apply_alternating_offset(model, x, shift_to_int, employees, alt_emps, alt_last_weekend, num_weeks, days_per_week):
    for emp in alt_emps:
        e = employees.index(emp)
        last_state = alt_last_weekend.get(emp, None)
        if last_state == "off":
            model.Add(x[0, days_per_week - 1, e] != shift_to_int["D/O"])
            if num_weeks > 1:
                model.Add(x[1, 0, e] != shift_to_int["D/O"])
        elif last_state == "on":
            model.Add(x[0, days_per_week - 1, e] == shift_to_int["D/O"])
            if num_weeks > 1:
                model.Add(x[1, 0, e] == shift_to_int["D/O"])

def add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alt_emps, working_slacks):
    objective_terms = []
    weekend_bonus_full = 5000
    weekend_bonus_partial = 2500
    for w in range(num_weeks):
        for e, emp in enumerate(employees):
            if emp in alt_emps:
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
    SLACK_WEIGHT = 10000
    if working_slacks:
        objective_terms.append(-SLACK_WEIGHT * sum(working_slacks))
    model.Maximize(sum(objective_terms))

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
            header = ["Name"] + [f"{(week_start + datetime.timedelta(days=d)).strftime('%a %d/%m')}" for d in range(days_per_week)]
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

# --------------------------------------------------------------------------------
# 6) MAIN EXECUTION
# --------------------------------------------------------------------------------
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "output")

    rules_filepath = os.path.join(script_dir, "Rules.json")
    required_rules, preferred_rules = load_rules(rules_filepath)
    with open(rules_filepath, "r") as f:
        rules_data = json.load(f)

    shift_leaders = rules_data.get("employees-shift_leaders", [])
    stepup_employees = rules_data.get("employees-step_up", [])
    employees = shift_leaders + [emp for emp in stepup_employees if emp not in shift_leaders]

    latest_csv = find_latest_csv(output_dir)
    prev_consecutive_dict = parse_last_week_consecutive_days(latest_csv, employees)
    alt_last_weekend_dict = parse_last_week_alternating(latest_csv, employees)

    num_weeks = 4
    days_per_week = 7
    shifts = ["E", "M", "L", "D/O", "H"]
    shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3, "H": 4}
    int_to_shift = {0: "E", 1: "M", 2: "L", 3: "D/O", 4: "H"}

    model, x = initialize_model(num_weeks, days_per_week, employees)

    add_allowed_shifts(model, required_rules, employees, shift_to_int, x, num_weeks, days_per_week)
    add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, employees)
    add_stepup_priority(model, x, shift_to_int, num_weeks, days_per_week, employees, stepup_employees)
    working_slacks = add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, num_weeks, days_per_week)
    temp_filepath = os.path.join(script_dir, "Temporary Rules.json")
    temp_rules = load_temporary_rules(temp_filepath)
    add_temporary_constraints(model, x, employees, temp_rules, num_weeks, days_per_week, shift_to_int)

    alt_employees = required_rules.get("Every other weekend off", [])
    if alt_employees:
        # Apply alternating offset from previous rota if available
        apply_alternating_offset(model, x, shift_to_int, employees, alt_employees, alt_last_weekend_dict, num_weeks, days_per_week)
        enforce_strict_alternating_weekends(model, x, shift_to_int, num_weeks, days_per_week, employees, alt_employees)

    add_no_late_to_early_constraint(model, x, shift_to_int, num_weeks, days_per_week, employees)
    add_consecutive_day_limit_with_offset(model, x, shift_to_int, num_weeks, days_per_week, employees, prev_consecutive_dict, max_consecutive=6)
    add_objective(model, x, shift_to_int, num_weeks, days_per_week, employees, preferred_rules, alt_employees, working_slacks)

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
        output_file = os.path.join(output_dir, f"Rota - {out_date_str}.csv")
        write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees, stepup_employees)
        print("Solution found. Wrote to:", os.path.abspath(output_file))
    else:
        print("No solution found.")

if __name__ == "__main__":
    main()
