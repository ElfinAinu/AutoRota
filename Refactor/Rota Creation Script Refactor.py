from ortools.sat.python import cp_model
import datetime
import csv
import os
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
json_file = os.path.join(script_dir, "Re Refactored Rules.json")
with open(json_file, "r") as f:
    refactored_rules = json.load(f)
required_rules = refactored_rules["Rules"]["required"]
preferred_rules = refactored_rules["Rules"].get("preferred", {})

day_name_to_index = {
    "Sunday": 0,
    "Monday": 1,
    "Tuesday": 2,
    "Wednesday": 3,
    "Thursday": 4,
    "Friday": 5,
    "Saturday": 6
}

num_weeks = 4
days_per_week = 7
employees = ["Jennifer", "Luke", "Senaka", "Stacey", "Callum"]
num_employees = len(employees)

shifts = ["E", "M", "L", "D/O"]  # 0=E,1=M,2=L,3=D/O
shift_to_int = {"E": 0, "M": 1, "L": 2, "D/O": 3}
int_to_shift = {0: "E", 1: "M", 2: "L", 3: "D/O"}

model = cp_model.CpModel()

# Decision variables: x[w, d, e] is the shift of employee e on week w, day d.
x = {}
for w in range(num_weeks):
    for d in range(days_per_week):
        for e in range(num_employees):
            x[w, d, e] = model.NewIntVar(0, 3, f"x[{w},{d},{e}]")

# Helper booleans: work[w,d,e] = True if shift != D/O.
work = {}
for w in range(num_weeks):
    for d in range(days_per_week):
        for e in range(num_employees):
            work[w, d, e] = model.NewBoolVar(f"work[{w},{d},{e}]")
            model.Add(x[w, d, e] != shift_to_int["D/O"]).OnlyEnforceIf(work[w, d, e])
            model.Add(x[w, d, e] == shift_to_int["D/O"]).OnlyEnforceIf(work[w, d, e].Not())

###############################################################################
# 1) Daily coverage: at least one Early and one Late each day.
###############################################################################
for w in range(num_weeks):
    for d in range(days_per_week):
        early_bools = []
        late_bools = []
        for e in range(num_employees):
            is_early = model.NewBoolVar(f"is_early_{w}_{d}_{e}")
            is_late = model.NewBoolVar(f"is_late_{w}_{d}_{e}")
            model.Add(x[w, d, e] == shift_to_int["E"]).OnlyEnforceIf(is_early)
            model.Add(x[w, d, e] != shift_to_int["E"]).OnlyEnforceIf(is_early.Not())
            model.Add(x[w, d, e] == shift_to_int["L"]).OnlyEnforceIf(is_late)
            model.Add(x[w, d, e] != shift_to_int["L"]).OnlyEnforceIf(is_late.Not())
            early_bools.append(is_early)
            late_bools.append(is_late)
        model.Add(sum(early_bools) >= 1)
        model.Add(sum(late_bools) >= 1)

###############################################################################
# 2) Weekly day counts:
#    - Everyone except Callum works exactly 5 days per week.
#    - Callum works at most 2 days per week, and optionally at least 1 overall.
###############################################################################
callum_idx = employees.index("Callum")
for w in range(num_weeks):
    for e in range(num_employees):
        day_work = [work[w, d, e] for d in range(days_per_week)]
        if e == callum_idx:
            model.Add(sum(day_work) <= 2)
        else:
            model.Add(sum(day_work) == 5)

# Optionally ensure Callum works at least once in total:
model.Add(
    sum(work[w, d, callum_idx] for w in range(num_weeks) for d in range(days_per_week))
    >= 1
)

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
        for e in range(num_employees):
            global_work[i, e] = model.NewBoolVar(f"global_work[{i},{e}]")
            model.Add(global_work[i, e] == 1).OnlyEnforceIf(work[w, d, e])
            model.Add(global_work[i, e] == 0).OnlyEnforceIf(work[w, d, e].Not())

# Hard: no 7 in a row => in any 7 consecutive days, sum <= 6.
for e in range(num_employees):
    for i in range(total_days - 6):
        model.Add(sum(global_work[j, e] for j in range(i, i + 7)) <= 6)

# Soft: if exactly 6 in a row in any 6 consecutive days, we penalize it.
# We'll create a boolean "six_in_a_row[i,e]" that is True if i..i+5 are all working.
# Then we subtract a large penalty from the objective for each occurrence.
six_in_a_row = {}
for e in range(num_employees):
    for i in range(total_days - 5):
        # i..i+5 is a 6-day block
        six_in_a_row[i, e] = model.NewBoolVar(f"six_in_a_row_{i}_{e}")
        model.AddBoolAnd([global_work[k, e] for k in range(i, i + 6)]).OnlyEnforceIf(six_in_a_row[i, e])
        model.AddBoolOr([global_work[k, e].Not() for k in range(i, i + 6)]).OnlyEnforceIf(six_in_a_row[i, e].Not())

###############################################################################
# 4) Employee-specific required rules from JSON
###############################################################################
if "Working Days" in required_rules:
    for e, emp in enumerate(employees):
        if emp in required_rules["Working Days"]:
            required_days = required_rules["Working Days"][emp]
            if emp == "Callum":
                for w in range(num_weeks):
                    model.Add(sum(work[w, d, e] for d in range(days_per_week)) <= required_days)
            else:
                for w in range(num_weeks):
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
        for w in range(num_weeks - 1):
            if w % 2 == 1:
                model.Add(x[w, 6, e] == shift_to_int["D/O"])
                model.Add(x[w+1, 0, e] == shift_to_int["D/O"])


###############################################################################
# 5) No Late-to-Early across week boundaries:
#    If an employee works Late on Saturday, they cannot do Early on Sunday of next week.
###############################################################################
for e in range(num_employees):
    for w in range(num_weeks - 1):
        was_late = model.NewBoolVar(f"sat_late_{w}_{e}")
        model.Add(x[w, 6, e] == shift_to_int["L"]).OnlyEnforceIf(was_late)
        model.Add(x[w, 6, e] != shift_to_int["L"]).OnlyEnforceIf(was_late.Not())
        # If Saturday was late, Sunday cannot be Early.
        model.Add(x[w+1, 0, e] != shift_to_int["E"]).OnlyEnforceIf(was_late)

###############################################################################
# 6) Soft constraints from JSON preferences plus penalty for 6_in_a_row
###############################################################################
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

# Big penalty for any instance of 6_in_a_row
BIG_PENALTY = 1000
obj_expr = []
obj_expr.append(sum(prefs))  # we want to maximize this
penalties = []
for e in range(num_employees):
    for i in range(total_days - 5):
        # Each time six_in_a_row is True, subtract BIG_PENALTY
        # We'll store "neg_var = model.NewIntVar(...)" approach is simpler with the linear sum
        # but we can do a single expression: sum(...) - BIG_PENALTY * six_in_a_row[i,e].
        # CP-SAT supports negative coefficients in the objective expression.
        penalties.append(six_in_a_row[i, e] * BIG_PENALTY)

model.Maximize(sum(obj_expr) - sum(penalties))

###############################################################################
# Solve and output
###############################################################################
solver = cp_model.CpSolver()
status = solver.Solve(model)

if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    # Build schedule
    schedule = {}
    for w in range(num_weeks):
        schedule[w] = {}
        for d in range(days_per_week):
            schedule[w][d] = {}
            for e, emp in enumerate(employees):
                val = solver.Value(x[w, d, e])
                schedule[w][d][emp] = int_to_shift[val]

    output_file = "rota.csv"
    start_date = datetime.datetime.strptime("23/02/2025", "%d/%m/%Y")

    with open(output_file, mode="w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for w in range(num_weeks):
            # Header
            week_start = start_date + datetime.timedelta(days=w * days_per_week)
            header = ["Name"]
            for d in range(days_per_week):
                current_date = week_start + datetime.timedelta(days=d)
                day_abbr = current_date.strftime("%a")
                header.append(f"{day_abbr} - {current_date.strftime('%d/%m')}")
            writer.writerow(header)

            # Rows
            for e, emp in enumerate(employees):
                row = [emp]
                for d in range(days_per_week):
                    shift_str = schedule[w][d][emp]
                    # If Callum is off, blank out
                    if emp == "Callum" and shift_str == "D/O":
                        row.append("")
                    else:
                        row.append(shift_str)
                writer.writerow(row)
            writer.writerow([])

    print("Solution found. Wrote to:", os.path.abspath(output_file))
    # Show the penalty for 6-in-a-row, if any:
    six_in_a_row_count = sum(solver.Value(six_in_a_row[i,e]) for e in range(num_employees) for i in range(total_days - 5))
    if six_in_a_row_count > 0:
        print(f"Number of 6-in-a-row occurrences: {six_in_a_row_count} (only used if no other solution).")
else:
    print("No solution found.")
