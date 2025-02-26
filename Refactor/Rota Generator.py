from ortools.sat.python import cp_model
import datetime
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
    model = cp_model.CpModel()
    num_employees = len(employees)
    x = {}
    work = {}
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e in range(num_employees):
                x[w, d, e] = model.NewIntVar(0, 3, f"x[{w},{d},{e}]")
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
                # For each day and shift, compute the number of shift leaders assigned that shift.
                indicators = []
                for i in range(len(shift_leaders)):  # assume shift leaders are at the start of employees list
                    ind = model.NewBoolVar(f"dup_indicator_{shift}_w{w}_d{d}_{i}")
                    model.Add(x[w, d, i] == shift_to_int[shift]).OnlyEnforceIf(ind)
                    model.Add(x[w, d, i] != shift_to_int[shift]).OnlyEnforceIf(ind.Not())
                    indicators.append(ind)
                # Let count_expr = sum(indicators)
                # Create an auxiliary variable to represent the extra assignments above 1.
                dup_aux = model.NewIntVar(0, len(shift_leaders) - 1, f"dup_aux_{shift}_w{w}_d{d}")
                # Enforce: dup_aux = max(0, (sum(indicators) - 1))
                model.Add(dup_aux + 1 >= sum(indicators))
                model.Add(dup_aux >= sum(indicators) - 1)
                duplicate_penalty_expr += duplicate_penalty_factor * dup_aux
    return duplicate_penalty_expr

num_weeks = 4
days_per_week = 7
employees = ["Jennifer", "Luke", "Senaka", "Stacey", "Callum"]
shifts = ["E", "M", "L", "D/O"]
shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3}
int_to_shift = {0: "E", 1: "M", 2: "L", 3: "D/O"}
model, x, work, global_work, total_days = initialize_model(num_weeks, days_per_week, employees, shift_to_int)

###############################################################################
# Add Weekend-off Constraint for Shift Leaders
###############################################################################
def add_weekend_off_constraints(model, x, num_weeks, days_per_week, employees, shift_to_int, shift_leaders):
    weekend_off_indicators = {}
    weekend_slacks = {}
    for emp in shift_leaders:
        e = employees.index(emp)
        emp_indicators = []
        # Loop over weeks 0 to num_weeks-2 so that we can pair Saturday and the next Sunday's off
        for w in range(num_weeks - 1):
            weekend_off = model.NewBoolVar(f"weekend_off_{emp}_{w}")
            # If weekend_off is true then Saturday of week w and Sunday of week w+1 must be off.
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"]).OnlyEnforceIf(weekend_off)
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(weekend_off)
            # Otherwise, at least one of these days is not off.
            not_sat_off = model.NewBoolVar(f"not_off_sat_{emp}_{w}")
            model.Add(x[w, days_per_week - 1, e] != shift_to_int["D/O"]).OnlyEnforceIf(not_sat_off)
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"]).OnlyEnforceIf(not_sat_off.Not())
            
            not_sun_off = model.NewBoolVar(f"not_off_sun_{emp}_{w}")
            model.Add(x[w+1, 0, e] != shift_to_int["D/O"]).OnlyEnforceIf(not_sun_off)
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(not_sun_off.Not())
            
            model.AddBoolOr([not_sat_off, not_sun_off]).OnlyEnforceIf(weekend_off.Not())
            emp_indicators.append(weekend_off)
        # Instead of a hard constraint, a slack variable allows the model to sacrifice a weekend off at heavy cost.
        slack = model.NewIntVar(0, 1, f"weekend_slack_{emp}")
        model.Add(sum(emp_indicators) + slack >= 1)
        weekend_slacks[emp] = slack
        weekend_off_indicators[emp] = emp_indicators
    return weekend_off_indicators, weekend_slacks

def add_weekend_shift_restrictions(model, x, days_per_week, num_weeks, employees, shift_to_int, shift_leaders):
    for w in range(num_weeks):
        for d in [0, days_per_week - 1]:  # Sunday and Saturday
            for emp in shift_leaders:
                e = employees.index(emp)
                model.Add(x[w, d, e] != shift_to_int["M"])
# 1) Daily coverage: at least one Early and one Late each day.
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
# 2) Weekly day counts:
#    - Everyone except Callum works exactly 5 days per week.
#    - Callum works at most 2 days per week, and optionally at least 1 overall.
###############################################################################
def add_weekly_work_constraints(model, work, num_weeks, days_per_week, employees, stepup_employees):
    for w in range(num_weeks):
        for e in range(len(employees)):
            day_work = [work[w, d, e] for d in range(days_per_week)]
            # If the employee is a step-up, restrict them to at most 2 workdays;
            # otherwise, they must work exactly 5 days per week.
            if employees[e] in stepup_employees:
                model.Add(sum(day_work) <= 2)
            else:
                model.Add(sum(day_work) == 5)
    # Ensure each step-up works at least one day overall.
    for emp in stepup_employees:
        e = employees.index(emp)
        model.Add(sum(work[w, d, e] for w in range(num_weeks) for d in range(days_per_week)) >= 1)

###############################################################################
# 3) Consecutive days constraints:
#    - Hard: No 7 in a row.
#    - Soft: Discourage 6 in a row by penalizing it in the objective.
###############################################################################
total_days = num_weeks * days_per_week  # 28
# Create global_work[i,e] for i in [0..27].
global_work = {}
def day_index(w, d):
    return w * days_per_week + d

for w in range(num_weeks):
    for d in range(days_per_week):
        i = day_index(w, d)
        for e in range(len(employees)):
            global_work[i, e] = model.NewBoolVar(f"global_work[{i},{e}]")
            model.Add(global_work[i, e] == 1).OnlyEnforceIf(work[w, d, e])
            model.Add(global_work[i, e] == 0).OnlyEnforceIf(work[w, d, e].Not())

def add_consecutive_day_constraints(model, global_work, total_days, num_employees, days_per_week):
    # Hard: no 7 in a row.
    for e in range(num_employees):
        for i in range(total_days - 6):
            model.Add(sum(global_work[j, e] for j in range(i, i + 7)) <= 6)
    # Soft: Track six-in-a-row occurrences.
    six_in_a_row = {}
    for e in range(num_employees):
        for i in range(total_days - 5):
            six_in_a_row[i, e] = model.NewBoolVar(f"six_in_a_row_{i}_{e}")
            model.AddBoolAnd([global_work[k, e] for k in range(i, i + 6)]).OnlyEnforceIf(six_in_a_row[i, e])
            model.AddBoolOr([global_work[k, e].Not() for k in range(i, i + 6)]).OnlyEnforceIf(six_in_a_row[i, e].Not())
    return six_in_a_row

six_in_a_row = add_consecutive_day_constraints(model, global_work, total_days, len(employees), days_per_week)

###############################################################################
# 4) Employee-specific required rules from JSON
###############################################################################
def add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, work, num_weeks, days_per_week):
    if "Working Days" in required_rules:
        for e, emp in enumerate(employees):
            if emp in required_rules["Working Days"]:
                required_days = required_rules["Working Days"][emp]
                for w in range(num_weeks):
                    if emp == "Callum":
                        model.Add(sum(work[w, d, e] for d in range(days_per_week)) <= required_days)
                    else:
                        model.Add(sum(work[w, d, e] for d in range(days_per_week)) == required_days)
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            e = employees.index(emp)
            day_idx = day_name_to_index[day]
            for w in range(num_weeks):
                model.Add(x[w, day_idx, e] == shift_to_int["D/O"])
    if "Every other weekend off" in required_rules:
        for emp in required_rules["Every other weekend off"]:
            e = employees.index(emp)
            weekend_off_vars = []
            # For each weekend defined by week i (Saturday) and week i+1 (Sunday)
            for i in range(num_weeks - 1):
                weekend_off_i = model.NewBoolVar(f"{emp}_weekend_off_{i}")
                # Enforce that if weekend_off_i is true then both Saturday of week i and Sunday of week i+1 are off
                model.Add(x[i, days_per_week - 1, e] == shift_to_int["D/O"]).OnlyEnforceIf(weekend_off_i)
                model.Add(x[i, days_per_week - 1, e] != shift_to_int["D/O"]).OnlyEnforceIf(weekend_off_i.Not())
                model.Add(x[i+1, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(weekend_off_i)
                model.Add(x[i+1, 0, e] != shift_to_int["D/O"]).OnlyEnforceIf(weekend_off_i.Not())
                weekend_off_vars.append(weekend_off_i)
            # Enforce that exactly the required number of weekends are off.
            # (For example, if there are 3 weekends (num_weeks - 1 = 3), require 3//2 = 1 full weekend off.)
            required_off = (num_weeks - 1) // 2
            model.Add(sum(weekend_off_vars) >= required_off)

def add_allowed_shifts(model, required_rules, employees, shift_to_int, x, work, num_weeks, days_per_week):
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                allowed_set = set()
                if emp in required_rules.get("Will Work Late", []):
                    allowed_set.add(shift_to_int["L"])
                if emp in required_rules.get("Will Work Middle", []):
                    allowed_set.add(shift_to_int["M"])
                if emp in required_rules.get("Will work Early", []):
                    allowed_set.add(shift_to_int["E"])
                if allowed_set:
                    for shift in ["E", "M", "L"]:
                        if shift_to_int[shift] not in allowed_set:
                            model.Add(x[w, d, e] != shift_to_int[shift]).OnlyEnforceIf(work[w, d, e])

def add_unique_shift_leader_constraints(model, x, num_weeks, days_per_week, shift_leaders, shift_to_int):
    num_shift_leaders = len(shift_leaders)
    for w in range(num_weeks):
        for d in range(days_per_week):
            for shift in ["E", "M", "L"]:
                indicators = []
                for i, emp in enumerate(shift_leaders):  # assume these are first in the employees list
                    indicator = model.NewBoolVar(f"unique_{shift}_w{w}_d{d}_leader{i}")
                    model.Add(x[w, d, i] == shift_to_int[shift]).OnlyEnforceIf(indicator)
                    model.Add(x[w, d, i] != shift_to_int[shift]).OnlyEnforceIf(indicator.Not())
                    indicators.append(indicator)
                # model.Add(sum(indicators) <= 1)
                # The unique shift leader constraint is removed to avoid overconstraining.
###############################################################################
# 5) No Late-to-Early across week boundaries:
#    If an employee works Late on Saturday, they cannot do Early on Sunday of next week.
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

# Define shift leaders based on the JSON (or hard-code if needed)
shift_leaders = ["Jennifer", "Luke", "Senaka", "Stacey"]
weekend_off_indicators, weekend_slacks = add_weekend_off_constraints(model, x, num_weeks, days_per_week, employees, shift_to_int, shift_leaders)

###############################################################################
# 6) Soft constraints from JSON preferences plus penalty for 6_in_a_row
###############################################################################
def add_preferred_constraints_and_objective(model, preferred_rules, employees, shift_to_int, num_weeks, days_per_week, x, six_in_a_row, total_days, weekend_off_indicators, weekend_slacks, stepup_employees):
    PREFERENCE_WEIGHT = 50000
    prefs = []
    if "Late Shifts" in preferred_rules:
        for emp in preferred_rules["Late Shifts"]:
            e = employees.index(emp)
            for w in range(num_weeks):
                for d in range(days_per_week):
                    var_late = model.NewBoolVar(f"{emp.lower()}_late_pref_{w}_{d}")
                    model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(var_late)
                    model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(var_late.Not())
                    prefs.append(var_late)
    if "Early Shifts" in preferred_rules:
        for emp in preferred_rules["Early Shifts"]:
            e = employees.index(emp)
            for w in range(num_weeks):
                for d in range(days_per_week):
                    var_early = model.NewBoolVar(f"{emp.lower()}_early_pref_{w}_{d}")
                    model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(var_early)
                    model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(var_early.Not())
                    prefs.append(var_early)
    if "Middle Shifts" in preferred_rules:
        for emp in preferred_rules["Middle Shifts"]:
            e = employees.index(emp)
            for w in range(num_weeks):
                for d in range(days_per_week):
                    var_middle = model.NewBoolVar(f"{emp.lower()}_middle_pref_{w}_{d}")
                    model.Add(x[w, d, e] == shift_to_int["M"]).OnlyEnforceIf(var_middle)
                    model.Add(x[w, d, e] != shift_to_int["M"]).OnlyEnforceIf(var_middle.Not())
                    prefs.append(var_middle)
    BIG_PENALTY = 1000
    WEEKEND_BONUS = 6000  # Adjusted bonus to balance preference
    WEEKEND_PENALTY = 1500  # Adjusted penalty to balance preference
    weekend_penalty_term = sum(weekend_slacks[emp] for emp in weekend_slacks)
    obj_expr = PREFERENCE_WEIGHT * sum(prefs)
    penalties = sum(six_in_a_row[i, e] * BIG_PENALTY for e in range(len(employees)) for i in range(total_days - 5))
    # Penalize working days for step-up employees.
    stepup_penalty_factor = 200
    weekend_coverage_bonus = []
    for w in range(num_weeks):
        for d in [0, days_per_week - 1]:
            e = employees.index("Callum")
            is_weekend_coverage = model.NewBoolVar(f"callum_weekend_coverage_{w}_{d}")
            model.Add(x[w, d, e] != shift_to_int["D/O"]).OnlyEnforceIf(is_weekend_coverage)
            model.Add(x[w, d, e] == shift_to_int["D/O"]).OnlyEnforceIf(is_weekend_coverage.Not())
            weekend_coverage_bonus.append(is_weekend_coverage)
    stepup_penalty = 0
    for emp in stepup_employees:
        e = employees.index(emp)
        for w in range(num_weeks):
            for d in range(days_per_week):
                stepup_penalty += work[w, d, e]
    # Modify the maximize to subtract the stepup penalty as well.
    # Accumulate bonus from weekend off indicators for each shift leader.
    weekend_bonus = sum(weekend for emp in weekend_off_indicators for weekend in weekend_off_indicators[emp])
  
    DUPLICATE_PENALTY = 1000
    duplicate_penalty = calc_duplicate_shift_leader_penalty(model, x, shift_to_int, num_weeks, days_per_week, shift_leaders, DUPLICATE_PENALTY)

    final_obj = cp_model.LinearExpr.Sum([
        obj_expr,
        -penalties,
        -stepup_penalty_factor * stepup_penalty,
        WEEKEND_BONUS * weekend_bonus,
        -WEEKEND_PENALTY * weekend_penalty_term,
        - duplicate_penalty
    ])
    model.Maximize(final_obj)


###############################################################################
# Solve and output
###############################################################################
solver = cp_model.CpSolver()
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
                    if emp == "Callum" and shift_str == "D/O":
                        row.append("")
                    else:
                        row.append(shift_str)
                writer.writerow(row)
            writer.writerow([])
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    json_file = os.path.join(script_dir, "Rules.json")
    required_rules, preferred_rules = load_rules(json_file)

    temp_file = os.path.join(script_dir, "Temporary Rules.json")
    temporary_rules = load_temporary_rules(temp_file)

    num_weeks = 4
    days_per_week = 7
    # Load from JSON: assume the loaded JSON is stored in full_json
    with open(json_file, "r") as f:
        full_json = json.load(f)
    shift_leaders = full_json.get("employees-shift_leaders", [])
    stepup_employees = full_json.get("employees-step_up", [])
    # To keep the employees order as desired:
    employees = shift_leaders + stepup_employees
    shifts = ["E", "M", "L", "D/O"]
    shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3}
    int_to_shift = {0: "E", 1: "M", 2: "L", 3: "D/O"}
    day_name_to_index = {
        "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
        "Thursday": 4, "Friday": 5, "Saturday": 6
    }

    model, x, work, global_work, total_days = initialize_model(num_weeks, days_per_week, employees, shift_to_int)
    add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, len(employees))
    add_weekly_work_constraints(model, work, num_weeks, days_per_week, employees, stepup_employees)
    six_in_a_row = add_consecutive_day_constraints(model, global_work, total_days, len(employees), days_per_week)
    add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, work, num_weeks, days_per_week)
    add_allowed_shifts(model, required_rules, employees, shift_to_int, x, work, num_weeks, days_per_week)
    add_week_boundary_constraints(model, x, shift_to_int, num_weeks, employees)
    add_weekend_shift_restrictions(model, x, days_per_week, num_weeks, employees, shift_to_int, shift_leaders)
    add_unique_shift_leader_constraints(model, x, num_weeks, days_per_week, shift_leaders, shift_to_int)

    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift)
        start_date = datetime.datetime.strptime("23/02/2025", "%d/%m/%Y")
        output_file = "rota.csv"
        write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees)
        print("Solution found. Wrote to:", os.path.abspath(output_file))
    else:
        print("No solution found.")
