# CLAUDE.md — PaceLabs Garmin Backend

This is the PaceLabs Garmin backend. A Python/Flask app hosted on Render free tier.

## What this does
Receives workout data from PaceLabs (Next.js app) and pushes structured workouts to Garmin Connect via the unofficial garminconnect Python library.

## Critical rules
- **Always push to git after edits** — Render auto-deploys on push
- **If dependencies change, tell Melwin to do "Clear build cache & deploy" on Render** — regular deploys use cached environment
- **garminconnect 0.3.x requires `step_order` on every step creation call**
- **`pydantic` must stay in requirements.txt** — garminconnect depends on it but doesn't declare it

## Structure field format from PaceLabs
Workouts arrive with structure like:
```json
[
  { "km": 2, "pace": "7:50/km", "type": "warmup" },
  { "km": 0.8, "pace": "5:50/km", "reps": 5, "type": "interval", "rest_seconds": 120 },
  { "km": 2.5, "pace": "7:50/km", "type": "cooldown" }
]
```
- Distance is `km` not `distance_km`
- Pace is a string like `"7:50/km"` not seconds — use `parse_pace()` to convert
- Reps field is `reps` not `repetitions`
- Rest is `rest_seconds` (time-based, not distance)

## Garmin pace target format (confirmed from real Garmin API)
```python
step.targetType = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}
step.targetValueOne = 1000 / pace_seconds * 0.95  # m/s, slower bound
step.targetValueTwo = 1000 / pace_seconds * 1.05  # m/s, faster bound
```
Garmin website shows kph — watch shows min/km. This is expected.

## Distance end conditions
```python
step.endCondition = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
step.endConditionValue = distance_metres
```

## Endpoints
- `GET /health` — health check
- `POST /connect` — connect Garmin account (email + password)
- `POST /push-workout` — push single workout
- `POST /push-week` — push all workouts for current week

## Tokens
Garmin OAuth tokens stored in Supabase `user_integrations` table, `garmin_tokens` jsonb field.

## Known issues
- Render free tier cold starts take 50+ seconds after inactivity
- Garmin rate limits after repeated failed logins — wait 24hrs if 429 errors appear
- Unofficial API may break on library updates — version is pinned in requirements.txt
