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
shifts = ["E", "M", "L", "D/O", "H"]
shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3, "H": 4}
int_to_shift = {0: "E", 1: "M", 2: "L", 3: "D/O", 4: "H"}
model, x, work, global_work, total_days = initialize_model(num_weeks, days_per_week, employees, shift_to_int)

###############################################################################
# Add Weekend-off Constraint for Shift Leaders
###############################################################################
def add_weekend_off_constraints(model, x, num_weeks, days_per_week, employees, shift_to_int, shift_leaders):
    weekend_full_indicators = {}
    weekend_sat_only_indicators = {}
    weekend_sun_only_indicators = {}
    for emp in shift_leaders:
        e = employees.index(emp)
        full_list = []
        sat_only_list = []
        sun_only_list = []
        for w in range(num_weeks - 1):
            # Reify Saturday off condition (Saturday of week w)
            sat_off = model.NewBoolVar(f"{emp}_sat_off_{w}")
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"]).OnlyEnforceIf(sat_off)
            model.Add(x[w, days_per_week - 1, e] != shift_to_int["D/O"]).OnlyEnforceIf(sat_off.Not())
            # Reify Sunday off condition (Sunday of week w+1)
            sun_off = model.NewBoolVar(f"{emp}_sun_off_{w}")
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(sun_off)
            model.Add(x[w+1, 0, e] != shift_to_int["D/O"]).OnlyEnforceIf(sun_off.Not())
            # Full weekend off indicator: both Saturday and Sunday off.
            weekend_full = model.NewBoolVar(f"{emp}_weekend_full_{w}")
            model.AddBoolAnd([sat_off, sun_off]).OnlyEnforceIf(weekend_full)
            # Partial: Saturday only off.
            weekend_sat_only = model.NewBoolVar(f"{emp}_weekend_sat_only_{w}")
            model.AddBoolAnd([sat_off, sun_off.Not()]).OnlyEnforceIf(weekend_sat_only)
            # Partial: Sunday only off.
            weekend_sun_only = model.NewBoolVar(f"{emp}_weekend_sun_only_{w}")
            model.AddBoolAnd([sun_off, sat_off.Not()]).OnlyEnforceIf(weekend_sun_only)
            full_list.append(weekend_full)
            sat_only_list.append(weekend_sat_only)
            sun_only_list.append(weekend_sun_only)
        weekend_full_indicators[emp] = full_list
        weekend_sat_only_indicators[emp] = sat_only_list
        weekend_sun_only_indicators[emp] = sun_only_list
    return weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators

def enforce_alternating_weekend_off_required(model, x, days_per_week, num_weeks, employees, shift_to_int, alternating_employees):
    for emp in alternating_employees:
        e = employees.index(emp)
        for w in range(0, num_weeks - 1, 2):
            # Ensure full weekend off in even weeks
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"])
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"])

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
    global slack_work_days   # Access the global slack_work_days list
    for w in range(num_weeks):
        for e in range(len(employees)):
            day_work = [work[w, d, e] for d in range(days_per_week)]
            # If the employee is a step-up, restrict them to at most 3 workdays;
            # otherwise, they must work between 4 and 6 days per week.
            if employees[e] in stepup_employees:
                model.Add(sum(day_work) <= 3)   # or whichever range you decide
            else:
                slack_work_days_var = model.NewIntVar(0, 1, f"slack_work_days_{w}_{e}")
                model.Add(sum(day_work) >= 4 - slack_work_days_var)
                model.Add(sum(day_work) <= 6 + slack_work_days_var)
                slack_work_days.append(slack_work_days_var)

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
    slack_seven_in_a_row = []
    for e in range(num_employees):
        for i in range(total_days - 6):
            slack_seven_in_a_row_var = model.NewIntVar(0, 1, f"slack_seven_in_a_row_{i}_{e}")
            model.Add(sum(global_work[j, e] for j in range(i, i + 7)) <= 6 + slack_seven_in_a_row_var)
            slack_seven_in_a_row.append(slack_seven_in_a_row_var)
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
                    if emp in stepup_employees:
                        model.Add(sum(work[w, d, e] for d in range(days_per_week)) <= required_days)
                    else:
                        model.Add(sum(work[w, d, e] for d in range(days_per_week)) == required_days)
    if "Days won't work" in required_rules:
        for emp, day in required_rules["Days won't work"].items():
            e = employees.index(emp)
            day_idx = day_name_to_index[day]
            for w in range(num_weeks):
                # Create a Boolean indicating compliance on this day.
                compliance = model.NewBoolVar(f"compliance_{emp}_{w}_{day}")
                model.Add(x[w, day_idx, e] == shift_to_int["D/O"]).OnlyEnforceIf(compliance)
                model.Add(x[w, day_idx, e] != shift_to_int["D/O"]).OnlyEnforceIf(compliance.Not())
                days_wont_work_vars.append(compliance)

def add_allowed_shifts(model, required_rules, employees, shift_to_int, x, work, num_weeks, days_per_week):
    for w in range(num_weeks):
        for d in range(days_per_week):
            for e, emp in enumerate(employees):
                allowed_set = set()
                # Build allowed set based solely on the JSON lists.
                if emp in required_rules.get("Will Work Late", []):
                    allowed_set.add(shift_to_int["L"])
                if emp in required_rules.get("Will Work Middle", []):
                    allowed_set.add(shift_to_int["M"])
                if emp in required_rules.get("Will work Early", []):
                    allowed_set.add(shift_to_int["E"])
                # Enforce: If employee is scheduled to work, then the assigned shift must be among allowed_set.
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
weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators = add_weekend_off_constraints(model, x, num_weeks, days_per_week, employees, shift_to_int, shift_leaders)


###############################################################################
# 6) Soft constraints from JSON preferences plus penalty for 6_in_a_row
###############################################################################
def add_preferred_constraints_and_objective(model, preferred_rules, employees, shift_to_int, num_weeks, days_per_week, x, six_in_a_row, total_days, weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators, stepup_employees, shift_leaders, slack_weekend, slack_weekend_complement, slack_work_days, slack_seven_in_a_row):
    # --- Revised Objective Terms with a Hierarchy ---

    # 1. WEEKEND OFF TERMS – Highest Priority:
    #    For each shift leader, every full weekend off yields a huge reward.
    SLACK_PENALTY_WEIGHT = 10000
    WEEKEND_BONUS_FULL = 10000    # lower reward per full weekend off
    WEEKEND_BONUS_PARTIAL = 5000   # lower reward per singular weekend day off
    weekend_full_reward = sum(ind for emp in weekend_full_indicators for ind in weekend_full_indicators[emp])
    weekend_partial_reward = sum(ind for emp in weekend_sat_only_indicators for ind in weekend_sat_only_indicators[emp]) + \
                             sum(ind for emp in weekend_sun_only_indicators for ind in weekend_sun_only_indicators[emp])
    weekend_reward_term = WEEKEND_BONUS_FULL * weekend_full_reward + WEEKEND_BONUS_PARTIAL * weekend_partial_reward

    # 2. SHIFT PREFERENCES – Secondary Priority:
    late_pref_sum = 0
    early_pref_sum = 0
    middle_pref_sum = 0
    if "Late Shifts" in preferred_rules:
        for emp in preferred_rules["Late Shifts"]:
            e = employees.index(emp)
            for w in range(num_weeks):
                for d in range(days_per_week):
                    var_late = model.NewBoolVar(f"{emp.lower()}_late_pref_{w}_{d}")
                    model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(var_late)
                    model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(var_late.Not())
                    late_pref_sum += var_late
    if "Early Shifts" in preferred_rules:
        for emp in preferred_rules["Early Shifts"]:
            e = employees.index(emp)
            for w in range(num_weeks):
                for d in range(days_per_week):
                    var_early = model.NewBoolVar(f"{emp.lower()}_early_pref_{w}_{d}")
                    model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(var_early)
                    model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(var_early.Not())
                    early_pref_sum += var_early
    if "Middle Shifts" in preferred_rules:
        for emp in preferred_rules["Middle Shifts"]:
            e = employees.index(emp)
            for w in range(num_weeks):
                for d in range(days_per_week):
                    var_middle = model.NewBoolVar(f"{emp.lower()}_middle_pref_{w}_{d}")
                    model.Add(x[w, d, e] == shift_to_int["M"]).OnlyEnforceIf(var_middle)
                    model.Add(x[w, d, e] != shift_to_int["M"]).OnlyEnforceIf(var_middle.Not())
                    middle_pref_sum += var_middle
    LATE_PREF_WEIGHT = 2000
    EARLY_PREF_WEIGHT = 2000
    MIDDLE_PREF_WEIGHT = 2000
    preference_term = LATE_PREF_WEIGHT * late_pref_sum + EARLY_PREF_WEIGHT * early_pref_sum + MIDDLE_PREF_WEIGHT * middle_pref_sum

    # 3. STEP-UP DAY BONUS:
    #    Look into the 'Days' preference from JSON so that if a step-up employee prefers certain days, we reward that.
    STEPUP_DAY_BONUS_WEIGHT = 2000
    stepup_day_bonus = 0
    if "Days" in preferred_rules:
        for emp, pref_days in preferred_rules["Days"].items():
            # Only process if the employee is in the step-up list.
            if emp in stepup_employees:
                e = employees.index(emp)
                # Map preferred day names to indices.
                preferred_day_idxs = [day_name_to_index[day] for day in pref_days]
                for w in range(num_weeks):
                    for d in range(days_per_week):
                        if d in preferred_day_idxs:
                            var_pref_day = model.NewBoolVar(f"{emp.lower()}_preferred_day_{w}_{d}")
                            # Reward that the employee is assigned a non-off shift on a preferred day.
                            model.Add(x[w, d, e] != shift_to_int["D/O"]).OnlyEnforceIf(var_pref_day)
                            model.Add(x[w, d, e] == shift_to_int["D/O"]).OnlyEnforceIf(var_pref_day.Not())
                            stepup_day_bonus += var_pref_day

    # 4. STEP-UP PENALTY – Tertiary Priority:
    STEPUP_PENALTY_FACTOR = 10
    stepup_penalty = 0
    for emp in stepup_employees:
        e = employees.index(emp)
        for w in range(num_weeks):
            for d in range(days_per_week):
                stepup_penalty += work[w, d, e]

    # 5. OFF-DAY GROUPING PENALTY
    OFF_DAY_GROUPING_PENALTY = 50
    off_day_penalty_terms = []
    for e in range(len(employees)):
        if employees[e] in shift_leaders:
            for w in range(num_weeks):
                for d in range(1, days_per_week - 1):
                    off_day_var = model.NewBoolVar(f"off_day_penalty_{w}_{d}_{e}")
                    off_day_penalty_terms.append(off_day_var)
                    model.AddBoolAnd([work[w, d-1, e].Not(), work[w, d+1, e].Not()]).OnlyEnforceIf(off_day_var)
                    model.AddBoolOr([work[w, d-1, e], work[w, d+1, e]]).OnlyEnforceIf(off_day_var.Not())
    for e in range(len(employees)):
        if employees[e] in shift_leaders:
            for w in range(num_weeks):
                for d in range(1, days_per_week - 1):
                    model.AddBoolOr([work[w, d-1, e].Not(), work[w, d, e], work[w, d+1, e].Not()])

    # 6. OTHER PENALTIES (six in a row & duplicate shift leader assignments)
    off_day_penalty_expr = cp_model.LinearExpr.Sum(off_day_penalty_terms)
    SIX_IN_A_ROW_PENALTY = 20
    EXTRA_SHIFT_LEADER_PENALTY = 10
    six_penalties = 0
    for e in range(len(employees)):
        for i in range(total_days - 5):
            if employees[e] in shift_leaders:
                six_penalties += six_in_a_row[i, e] * (SIX_IN_A_ROW_PENALTY + EXTRA_SHIFT_LEADER_PENALTY)
            else:
                six_penalties += six_in_a_row[i, e] * SIX_IN_A_ROW_PENALTY
    DUPLICATE_PENALTY = 20
    duplicate_penalty = calc_duplicate_shift_leader_penalty(model, x, shift_to_int, num_weeks, days_per_week, shift_leaders, DUPLICATE_PENALTY)

    # 6. Final Objective Assembly – use a weighted sum that imposes our strict hierarchy:
    # --- (NEW) Daily Early/Late Preference Soft Penalization ---
    EARLY_EXCESS_PENALTY = 200
    LATE_EXCESS_PENALTY = 200
    extra_early_penalty_term = 0
    extra_late_penalty_term = 0
    for w in range(num_weeks):
        for d in range(days_per_week):
            early_indicators = []
            late_indicators = []
            for e in range(len(employees)):
                early_b = model.NewBoolVar(f"pref_early_{w}_{d}_{e}")
                late_b  = model.NewBoolVar(f"pref_late_{w}_{d}_{e}")
                model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(early_b)
                model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(early_b.Not())
                model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(late_b)
                model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(late_b.Not())
                early_indicators.append(early_b)
                late_indicators.append(late_b)
            early_count = model.NewIntVar(1, len(employees), f"early_count_{w}_{d}")
            late_count  = model.NewIntVar(1, len(employees), f"late_count_{w}_{d}")
            model.Add(early_count == sum(early_indicators))
            model.Add(late_count  == sum(late_indicators))
            extra_early = model.NewIntVar(0, len(employees)-1, f"extra_early_{w}_{d}")
            extra_late  = model.NewIntVar(0, len(employees)-1, f"extra_late_{w}_{d}")
            model.Add(extra_early == early_count - 1)
            model.Add(extra_late == late_count - 1)
            extra_early_penalty_term += extra_early
            extra_late_penalty_term  += extra_late
    # Soft penalty for non-compliance with 'Days won't work' rules.
    # (For each instance, if compliance is 0 (i.e. violation) then (1 - compliance) evaluates to 1.)
    DAYS_WONT_WORK_PENALTY = 10000
    days_wont_work_penalty_expr = cp_model.LinearExpr.Sum([1 - var for var in days_wont_work_vars])
    final_obj = cp_model.LinearExpr.Sum([
        - DAYS_WONT_WORK_PENALTY * days_wont_work_penalty_expr,
        - SLACK_PENALTY_WEIGHT * (sum(slack_weekend) + sum(slack_weekend_complement) + sum(slack_work_days) + sum(slack_seven_in_a_row)),
        # (a) Weekend off is top: reward (minus penalty if missing)
        weekend_reward_term,
        # (b) Next, individual shift preferences.
        preference_term,
        # (c) Then, the bonus for a step-up employee working on their preferred days.
        STEPUP_DAY_BONUS_WEIGHT * stepup_day_bonus,
        # (d) Then subtract step-up usage penalty.
        - STEPUP_PENALTY_FACTOR * stepup_penalty,
        # (e) Also subtract other penalties:
        - six_penalties,
        - duplicate_penalty,
        - OFF_DAY_GROUPING_PENALTY * off_day_penalty_expr
        # (f) NEW: Penalize extra early and late shifts beyond 1 per day.
        - EARLY_EXCESS_PENALTY * extra_early_penalty_term,
        - LATE_EXCESS_PENALTY * extra_late_penalty_term
    ])
    model.Maximize(final_obj)
    return final_obj


###############################################################################
# Solve and output
###############################################################################
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
def add_temporary_constraints(model, x, employees, temporary_rules, num_weeks, days_per_week, shift_to_int):
    # Extract the global start date from Temporary Rules (under "Everyone")
    global_rules = temporary_rules["Required"].get("Everyone", {})
    rota_start_str = global_rules.get("Start Date", "")
    if rota_start_str:
        rota_start = datetime.datetime.strptime(rota_start_str, "%Y/%m/%d")
    else:
        # Fallback if not provided:
        rota_start = datetime.datetime.today()

    # For each employee in the temporary rules (if defined)
    for emp in employees:
        if emp in temporary_rules["Required"]:
            emp_rules = temporary_rules["Required"][emp]
            e = employees.index(emp)
            # 2.a) Specific Days Off:
            # Expect "days off" to be a list of date strings in 'yyyy/mm/dd' format.
            days_off = emp_rules.get("days off", [])
            for day_str in days_off:
                if day_str:  # ignore empty strings
                    off_date = datetime.datetime.strptime(day_str, "%Y/%m/%d")
                    # Iterate over all rota days
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if current_date.date() == off_date.date():
                                # Force off-day (using "D/O") on that day.
                                model.Add(x[w, d, e] == shift_to_int["D/O"])
            # 2.b) Specific Shift Requirements:
            # Check if there is a specific shift (Early, Middle, or Late) requested on a given date.
            for shift_field in ["Early", "Middle", "Late"]:
                req_date_str = emp_rules.get(shift_field, "")
                if req_date_str:
                    req_date = datetime.datetime.strptime(req_date_str, "%Y/%m/%d")
                    for w in range(num_weeks):
                        for d in range(days_per_week):
                            current_date = rota_start + datetime.timedelta(days=w*days_per_week + d)
                            if current_date.date() == req_date.date():
                                # Force the required shift based on the field.
                                if shift_field == "Early":
                                    model.Add(x[w, d, e] == shift_to_int["E"])
                                elif shift_field == "Middle":
                                    model.Add(x[w, d, e] == shift_to_int["M"])
                                elif shift_field == "Late":
                                    model.Add(x[w, d, e] == shift_to_int["L"])
            # 2.c) Holiday Enforcement:
            # If the employee is on holiday, mark each day in the holiday range as "H".
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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    global stepup_employees  # Declare global variable
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
    # Define the global slack variables before any constraints are added that use them.
    slack_weekend = []
    slack_weekend_complement = []
    slack_work_days = []
    slack_seven_in_a_row = []
    days_wont_work_vars = []
    add_daily_coverage_constraints(model, x, shift_to_int, num_weeks, days_per_week, len(employees))
    add_weekly_work_constraints(model, work, num_weeks, days_per_week, employees, stepup_employees)
    six_in_a_row = add_consecutive_day_constraints(model, global_work, total_days, len(employees), days_per_week)
    add_employee_specific_constraints(model, required_rules, employees, day_name_to_index, shift_to_int, x, work, num_weeks, days_per_week)
    add_allowed_shifts(model, required_rules, employees, shift_to_int, x, work, num_weeks, days_per_week)
    weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators = add_weekend_off_constraints(model, x, num_weeks, days_per_week, employees, shift_to_int, shift_leaders)
    add_week_boundary_constraints(model, x, shift_to_int, num_weeks, employees)
    add_weekend_shift_restrictions(model, x, days_per_week, num_weeks, employees, shift_to_int, shift_leaders)
    add_temporary_constraints(model, x, employees, temporary_rules, num_weeks, days_per_week, shift_to_int)

    for emp in shift_leaders:
        if emp not in required_rules.get("Every other weekend off", []):
            e = employees.index(emp)
            weekend_off_indicators = []
            # Loop over all weeks (each week is Sunday to Saturday)
            for w in range(num_weeks):
                # Create indicator variables for Sunday off (day index 0) and Saturday off (day index 6)
                sunday_off = model.NewBoolVar(f"{emp}_Sunday_off_week_{w}")
                saturday_off = model.NewBoolVar(f"{emp}_Saturday_off_week_{w}")
                model.Add(x[w, 0, e] == shift_to_int["D/O"]).OnlyEnforceIf(sunday_off)
                model.Add(x[w, 0, e] != shift_to_int["D/O"]).OnlyEnforceIf(sunday_off.Not())
                model.Add(x[w, 6, e] == shift_to_int["D/O"]).OnlyEnforceIf(saturday_off)
                model.Add(x[w, 6, e] != shift_to_int["D/O"]).OnlyEnforceIf(saturday_off.Not())
                weekend_off_indicators.append(sunday_off)
                weekend_off_indicators.append(saturday_off)
            # Enforce that over the entire span at least one weekend day off is taken
            model.Add(sum(weekend_off_indicators) >= 1)
    add_unique_shift_leader_constraints(model, x, num_weeks, days_per_week, shift_leaders, shift_to_int)

    slack_weekend = []
    slack_weekend_complement = []
    slack_work_days = []
    slack_seven_in_a_row = []

    if "Every other weekend off" in required_rules:
        alternating_employees = required_rules["Every other weekend off"]
        enforce_alternating_weekend_off_required(model, x, days_per_week, num_weeks, employees, shift_to_int, alternating_employees)
    final_obj = add_preferred_constraints_and_objective(model, preferred_rules, employees, shift_to_int, num_weeks, days_per_week, x, six_in_a_row, total_days, weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators, stepup_employees, shift_leaders, slack_weekend, slack_weekend_complement, slack_work_days, slack_seven_in_a_row)

    solver = cp_model.CpSolver()
    solver.parameters.random_seed = int(datetime.datetime.now().timestamp() * 1000) % 2147483647
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift)
        global_temp = temporary_rules["Required"].get("Everyone", {})
        if "Start Date" in global_temp:
            start_date = datetime.datetime.strptime(global_temp["Start Date"], "%Y/%m/%d")
        else:
            start_date = datetime.datetime.strptime("23/02/2025", "%d/%m/%Y")  # fallback
        output_file = os.path.join(script_dir, "output", "rota.csv")
        write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees)
        print("Solution found. Wrote to:", os.path.abspath(output_file))
    else:
        print("No solution found.")
