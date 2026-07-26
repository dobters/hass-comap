# ComAp Home Assistant Integration

Home Assistant custom integration for ComAp generator controllers (InteliLite AMF25 and compatible) using the native TCP protocol.

## Features

- **Auto-discovery** of controllers on the local network via UDP broadcast
- **Dynamic entities** — sensors, binary sensors, numbers, and selects are generated from the controller's own configuration table (no hardcoded register lists)
- **Read + write** — setpoints are exposed as editable number/select entities
- **Alarms** — active alarm count sensor + HA events fired on alarm state changes
- **Fault Reset** button

## Supported Entities

| Platform | What | Notes |
|----------|------|-------|
| `sensor` | Numeric controller values (voltage, current, power, RPM, frequency, energy counters, …) | Read-only |
| `sensor` | Alarm count | Active alarm details in attributes |
| `binary_sensor` | Binary word values (per-bit) | Named positionally, e.g. *Controller I/O 1* |
| `number` | Numeric setpoints | Disabled by default if password-protected |
| `select` | STRING_LIST setpoints (Controller Mode, etc.) | Disabled by default if password-protected |
| `button` | Fault Reset | |

## Requirements

- Home Assistant 2025.6.0 or newer
- ComAp InteliLite AMF25 (or compatible) reachable over LAN on TCP port 23

## Installation via HACS

1. Add this repository as a custom repository in HACS (type: Integration)
2. Search for **ComAp** and install
3. Restart Home Assistant
4. Go to **Settings → Integrations → Add Integration → ComAp**

## Configuration

The setup flow discovers controllers on the network automatically. If none are found you can enter the IP address manually.

| Field | Description |
|-------|-------------|
| Host | IP address of the controller |
| Port | TCP port (default: `23`) |
| Access Code | Controller access code (default: `0`) |
| Write Password | Optional integer (0–9999). Required to write setpoints. |

After a successful connection the integration creates the entry with a default set of enabled entities (see [Default entities](#default-entities) below).

## Options

Open **Settings → Integrations → ComAp → Configure** to adjust:

| Option | Default | Description |
|--------|---------|-------------|
| Poll interval | 10 s | How often to read values from the controller |
| Configure entities | off | When enabled, exposes additional screens to individually enable or disable values and setpoints |

### Entity configuration

Checking **Configure entities** steps through one screen per group. Each screen lists every entity in that group with its controller number and name; select the ones to expose. Unchecking the option on a later visit leaves the current selection unchanged.

Default enabled groups are chosen to cover the most useful measurements for a typical AMF installation:

- **Values**: Generator, Mains, Load, Statistics, Controller I/O, IL Info, Engine
- **Setpoints**: Statistics, Basic Settings, AMF Settings

The *Not Used* group is excluded by default regardless of selection.

## Default entities

When first configured, entities from the default groups above are enabled. All others exist in the entity registry but are disabled. You can enable individual entities from the entity list, or use **Configure entities** in Options to bulk-manage them by group.

## Alarm events

In addition to the alarm count sensor, the integration fires Home Assistant events when alarm state changes between polls:

| Event | Fired when |
|-------|-----------|
| `comap_alarm_active` | A new active alarm appears |
| `comap_alarm_cleared` | A previously active alarm is no longer present |

Both events carry:

```yaml
entry_id: "..."      # config entry ID — use to filter when multiple controllers are configured
reason: "Generator Voltage L1-N"
prefix: "Wrn"        # Wrn / Sd / Boc / …
fault_code: 0        # raw ECU fault code (non-zero for CAN bus alarms)
source: 1
```

`comap_alarm_active` also includes `is_confirmed` (whether the alarm was already acknowledged on the panel at the time it was first seen).

### Example automation

```yaml
trigger:
  - platform: event
    event_type: comap_alarm_active
    event_data:
      prefix: Sd
action:
  - action: notify.mobile_app
    data:
      title: "Generator shutdown"
      message: "{{ trigger.event.data.reason }}"
```