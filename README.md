# AutoRota

An automated shift scheduling system that generates employee work rotas using constraint programming.

## Overview

AutoRota uses Google OR-Tools' constraint solver to create optimal employee schedules while respecting various work rules and preferences. It automatically assigns shifts (Early, Middle, Late) to employees across a 4-week period, ensuring fair distribution and compliance with workplace policies.

## Features

- **Constraint-based scheduling**: Enforces hard rules like no late-to-early shifts, maximum working days, and employee availability
- **Duty manager requirements**: Ensures proper coverage with designated duty managers and reserve employees
- **Preference optimization**: Considers employee preferences for specific shifts and days when possible
- **Consecutive workday limits**: Prevents employees from working more than 6 consecutive days
- **Weekend rotation**: Supports alternating weekend schedules for work-life balance
- **CSV output**: Generates easy-to-read schedules organized by week

## Installation

1. Install Python 3.8 or higher
2. Install required dependencies:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python "Rota Generator.py"
```

The system reads employee data and rules from `Rules.json` and `Temporary Rules.json`, then outputs a 4-week schedule to the `output/` directory.

## Configuration and Rules

### Setting the Start Date

The week you want to generate your rota for must be set in the `Temporary Rules.json` file:

```json
{
  "Required": {
    "Everyone": {
      "Start Date": "2025/03/30"  // Format: yyyy/mm/dd
    }
  }
}
```

**Important**: The system automatically checks the `output/` folder for the most recent rota file. It uses this to:
- Track consecutive working days from the previous week
- Maintain alternating weekend schedules
- Ensure continuity between rota periods

### Rules.json Structure

This file contains the core configuration for employees and scheduling constraints:

```json
{
  "employees-duty_manager": ["Jane", "Jill", "Jack", "John"],
  "employees-duty_manager-reserve": ["Bob"],
  "Rules": {
    "required": {
      "Working Days": {
        "Jane": 5,  // Duty managers must work exactly this many days
        "Bob": 2    // Reserves work up to this many days
      },
      "Days won't work": {
        "Jack": "Sunday",
        "Bob": "Saturday"
      },
      "Will Work Late": ["Bob", "Jane", "John"],
      "Will Work Middle": ["Jack", "John", "Jane"],
      "Will work Early": ["Jill", "Jack", "Jane"],
      "Every other weekend off": ["Jill"],
      "No Late to Early": ["Jane", "Jill", "Jack", "John", "Bob"]
    },
    "preferred": {
      "Late Shifts": ["Jane"],
      "Early Shifts": ["Jack"],
      "Middle Shifts": ["Jack", "John"]
    }
  }
}
```

### Temporary Rules.json Structure

This file handles temporary requirements like holidays and specific shift assignments:

```json
{
  "Required": {
    "Everyone": {
      "Start Date": "2025/03/30"
    },
    "Jane": {
      "days off": ["2025/04/05"],
      "Early": "2025/04/01",
      "Middle": "",
      "Late": "",
      "holiday": {
        "active": false,
        "start": "",
        "end": ""
      }
    }
  }
}
```

### Hard Rules (Built into the System)

1. **No one works more than 6 consecutive days**
2. **Duty manager coverage**: At least one early and one late shift must be covered daily
3. **Maximum 4 employees working per day**
4. **No middle shifts on weekends**
5. **Duty manager reserves only work when needed**
6. **Reserve employees show blank cells (not "D/O") on non-working days**

### How Previous Week Detection Works

When generating a new rota:

1. The system scans the `output/` directory for existing rota files
2. It identifies the most recent file by parsing the date in the filename (e.g., "Rota - 2025-03-02.csv")
3. It reads the last week of that rota to determine:
   - How many consecutive days each employee worked at the end
   - Who had the previous weekend off (for alternating schedules)
4. This information is used to ensure continuity:
   - If someone worked 6 days ending Saturday, they must have Sunday off
   - Alternating weekend patterns continue correctly

**Note**: If no previous rota exists, the system starts fresh with zero consecutive days for all employees.