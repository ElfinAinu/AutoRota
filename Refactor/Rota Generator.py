from ortools.sat.python import cp_model
import datetime
from ortools.sat import cp_model_pb2
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
        # For each consecutive pair of weeks (even week and the following odd week)
        for w in range(0, num_weeks - 1, 2):
            # Designated off days:
            # Even week: Saturday must be off.
            model.Add(x[w, days_per_week - 1, e] == shift_to_int["D/O"])
            # Odd week: Sunday must be off.
            model.Add(x[w+1, 0, e] == shift_to_int["D/O"])
            # Conversely, enforce that the complementary weekend days are working:
            # Even week: Sunday must be working.
            model.Add(x[w, 0, e] != shift_to_int["D/O"])
            # Odd week: Saturday must be working.
            model.Add(x[w+1, days_per_week - 1, e] != shift_to_int["D/O"])
        # If the horizon has an odd number of weeks, enforce that in the last week both weekend days are working.
        if num_weeks % 2 == 1:
            w = num_weeks - 1
            model.Add(x[w, 0, e] != shift_to_int["D/O"])
            model.Add(x[w, days_per_week - 1, e] != shift_to_int["D/O"])

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
                # --- Optional relaxation for step-ups: allow them an additional shift if needed.
                if emp in stepup_employees:
                    allowed_set.add(shift_to_int["E"])
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
def add_preferred_constraints_and_objective(model, preferred_rules, employees, shift_to_int, num_weeks, days_per_week, x, six_in_a_row, total_days, weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators, stepup_employees, shift_leaders):
    # --- Revised Objective Terms with a Hierarchy ---

    # 1. WEEKEND OFF TERMS – Highest Priority:
    #    For each shift leader, every full weekend off yields a huge reward.
    WEEKEND_BONUS_FULL = 500000    # reward per full weekend off achieved
    WEEKEND_BONUS_PARTIAL = 250000  # reward per singular weekend day off achieved
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
    LATE_PREF_WEIGHT = 150000
    EARLY_PREF_WEIGHT = 120000
    MIDDLE_PREF_WEIGHT = 150000
    preference_term = LATE_PREF_WEIGHT * late_pref_sum + EARLY_PREF_WEIGHT * early_pref_sum + MIDDLE_PREF_WEIGHT * middle_pref_sum

    # 3. CALLUM (or any step-up) DAY BONUS:
    #    Look into the 'Days' preference from JSON so that if a step-up (e.g., Callum) likes Friday/Sunday, we reward that.
    CALLUM_DAY_BONUS_WEIGHT = 100000
    callup_day_bonus = 0
    if "Days" in preferred_rules:
        # Create an inverse mapping for day names if needed.
        day_index_to_name = {v: k for k, v in day_name_to_index.items()}
        for emp, pref_days in preferred_rules["Days"].items():
            e = employees.index(emp)
            # Map preferred day names to their indices.
            preferred_day_idxs = [day_name_to_index[day] for day in pref_days]
            for w in range(num_weeks):
                for d in range(days_per_week):
                    if d in preferred_day_idxs:
                        var_pref_day = model.NewBoolVar(f"{emp.lower()}_preferred_day_{w}_{d}")
                        # Reward non-off assignment on that day.
                        model.Add(x[w, d, e] != shift_to_int["D/O"]).OnlyEnforceIf(var_pref_day)
                        model.Add(x[w, d, e] == shift_to_int["D/O"]).OnlyEnforceIf(var_pref_day.Not())
                        callup_day_bonus += var_pref_day

    # 4. STEP-UP PENALTY – Tertiary Priority:
    STEPUP_PENALTY_FACTOR = 600
    stepup_penalty = 0
    for emp in stepup_employees:
        e = employees.index(emp)
        for w in range(num_weeks):
            for d in range(days_per_week):
                stepup_penalty += work[w, d, e]

    # 5. OFF-DAY GROUPING PENALTY
    OFF_DAY_GROUPING_PENALTY = 2000
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
    SIX_IN_A_ROW_PENALTY = 1000
    EXTRA_SHIFT_LEADER_PENALTY = 500  # Additional penalty for any shift leader
    six_penalties = 0
    for e in range(len(employees)):
        for i in range(total_days - 5):
            if employees[e] in shift_leaders:
                six_penalties += six_in_a_row[i, e] * (SIX_IN_A_ROW_PENALTY + EXTRA_SHIFT_LEADER_PENALTY)
            else:
                six_penalties += six_in_a_row[i, e] * SIX_IN_A_ROW_PENALTY
    DUPLICATE_PENALTY = 1000
    duplicate_penalty = calc_duplicate_shift_leader_penalty(model, x, shift_to_int, num_weeks, days_per_week, shift_leaders, DUPLICATE_PENALTY)

    # 6. Final Objective Assembly – use a weighted sum that imposes our strict hierarchy:
    # --- (NEW) Daily Early/Late Preference Soft Penalization ---
    EARLY_EXCESS_PENALTY = 20000
    LATE_EXCESS_PENALTY = 20000
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
    final_obj = cp_model.LinearExpr.Sum([
        # (a) Weekend off is top: reward (minus penalty if missing)
        weekend_reward_term,
        # (b) Next, individual shift preferences.
        preference_term,
        # (c) Then, the bonus for a step-up (e.g., Callum) working on his preferred days.
        CALLUM_DAY_BONUS_WEIGHT * callup_day_bonus,
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
    weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators = add_weekend_off_constraints(model, x, num_weeks, days_per_week, employees, shift_to_int, shift_leaders)
    add_week_boundary_constraints(model, x, shift_to_int, num_weeks, employees)
    add_weekend_shift_restrictions(model, x, days_per_week, num_weeks, employees, shift_to_int, shift_leaders)
    add_unique_shift_leader_constraints(model, x, num_weeks, days_per_week, shift_leaders, shift_to_int)

    if "Every other weekend off" in required_rules:
        alternating_employees = required_rules["Every other weekend off"]
        enforce_alternating_weekend_off_required(model, x, days_per_week, num_weeks, employees, shift_to_int, alternating_employees)
    final_obj = add_preferred_constraints_and_objective(model, preferred_rules, employees, shift_to_int, num_weeks, days_per_week, x, six_in_a_row, total_days, weekend_full_indicators, weekend_sat_only_indicators, weekend_sun_only_indicators, stepup_employees, shift_leaders)

    solver = cp_model.CpSolver()
    solver.parameters.random_seed = int(datetime.datetime.now().timestamp() * 1000) % 2147483647
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = build_schedule(solver, x, num_weeks, days_per_week, employees, int_to_shift)
        start_date = datetime.datetime.strptime("23/02/2025", "%d/%m/%Y")
        output_file = os.path.join(script_dir, "output", "rota.csv")
        write_output_csv(schedule, output_file, start_date, num_weeks, days_per_week, employees)
        print("Solution found. Wrote to:", os.path.abspath(output_file))
    else:
        print("No solution found.")
