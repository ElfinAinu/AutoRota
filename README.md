# AutoRota

An automated shift scheduling system that generates employee work rotas using constraint programming.

## Overview

AutoRota uses Google OR-Tools' constraint solver to create optimal employee schedules while respecting various work rules and preferences. It automatically assigns shifts (Early, Middle, Late) to employees across a 4-week period, ensuring fair distribution and compliance with workplace policies.

## Features

- **Constraint-based scheduling**: Enforces hard rules like no late-to-early shifts, maximum working days, and employee availability
- **Shift leader requirements**: Ensures proper coverage with designated shift leaders and step-up employees
- **Preference optimization**: Considers employee preferences for specific shifts and days when possible
- **Consecutive workday limits**: Prevents employees from working more than 6 consecutive days
- **Weekend rotation**: Supports alternating weekend schedules for work-life balance
- **CSV output**: Generates easy-to-read schedules organized by week

## Usage

```bash
python "Rota Generator.py"
```

The system reads employee data and rules from `Rules.json` and `Temporary Rules.json`, then outputs a 4-week schedule to the `output/` directory.