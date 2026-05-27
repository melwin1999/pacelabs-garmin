# v2
from flask import Flask, request, jsonify
import json
import os
import pathlib
import tempfile
import urllib.request
import urllib.error

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

def supabase_request(method, path, body=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"Supabase error {e.code}: {e.read().decode()}")

def get_garmin_tokens():
    rows = supabase_request("GET", "user_integrations?provider=eq.garmin&select=garmin_email,garmin_tokens")
    if not rows:
        return None, None
    return rows[0].get("garmin_email"), rows[0].get("garmin_tokens")

def save_garmin_tokens(token_dir):
    token_file = pathlib.Path(token_dir) / "garmin_tokens.json"
    if token_file.exists():
        tokens = json.loads(token_file.read_text())
        supabase_request("PATCH", "user_integrations?provider=eq.garmin", {
            "garmin_tokens": tokens
        })

def get_garmin_client(email, tokens):
    from garminconnect import Garmin
    token_dir = pathlib.Path(tempfile.mkdtemp())
    token_file = token_dir / "garmin_tokens.json"
    token_file.write_text(json.dumps(tokens))
    client = Garmin(email=email, password=None, prompt_mfa=None)
    client.login(str(token_dir))
    return client, str(token_dir)

def parse_pace(pace_str):
    """Convert '7:50/km' string to seconds per km integer."""
    if not pace_str:
        return None
    try:
        pace_str = pace_str.replace("/km", "").strip()
        parts = pace_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None

def build_garmin_workout(workout):
    from garminconnect.workout import (
        RunningWorkout, WorkoutSegment,
        create_warmup_step, create_cooldown_step,
        create_interval_step, create_repeat_group,
    )
    wtype = workout.get("type", "easy")
    name = workout.get("name", "Run")
    distance_m = int((workout.get("distance_km") or 0) * 1000)
    pace_min = workout.get("pace_min_seconds")
    pace_max = workout.get("pace_max_seconds")
    structure = workout.get("structure") or []
    steps = []

    def make_target(pace_seconds=None):
        if pace_seconds:
            pace_per_meter = pace_seconds / 1000
            return {
                "workoutTargetTypeId": 6,
                "workoutTargetTypeKey": "pace.zone",
                "targetValueOne": pace_per_meter * 0.95,
                "targetValueTwo": pace_per_meter * 1.05,
            }
        return None

    dist_end_condition = {"conditionTypeId": 3, "conditionTypeKey": "distance"}

    if structure:
        order = 1
        for seg in structure:
            seg_type = seg.get("type", "interval")
            dist_m = float((seg.get("km") or seg.get("distance_km") or 0) * 1000)
            pace_seconds = parse_pace(seg.get("pace")) or seg.get("pace_seconds")
            reps = seg.get("reps") or seg.get("repetitions") or 1
            target = make_target(pace_seconds)
            end_val = dist_m or 600.0

            if seg_type == "warmup":
                steps.append(create_warmup_step(end_val, step_order=order, end_condition=dist_end_condition, target_type=target))
                order += 1
            elif seg_type == "cooldown":
                steps.append(create_cooldown_step(end_val, step_order=order, end_condition=dist_end_condition, target_type=target))
                order += 1
            elif seg_type == "interval" and reps > 1:
                inner = []
                inner_order = 1
                inner.append(create_interval_step(end_val, step_order=inner_order, end_condition=dist_end_condition, target_type=target))
                inner_order += 1
                rest_m = float(seg.get("rest_metres") or seg.get("rest_meters") or 200)
                inner.append(create_interval_step(rest_m, step_order=inner_order, end_condition=dist_end_condition))
                steps.append(create_repeat_group(reps, inner, step_order=order))
                order += 1
            else:
                steps.append(create_interval_step(end_val, step_order=order, end_condition=dist_end_condition, target_type=target))
                order += 1
    else:
        if wtype in ("easy", "long", "recovery"):
            target = make_target(pace_max)
            steps = [
                create_warmup_step(1000.0, step_order=1),
                create_interval_step(float(max(distance_m - 2000, 1000)), step_order=2, target_type=target),
                create_cooldown_step(1000.0, step_order=3),
            ]
        elif wtype in ("tempo", "threshold", "fartlek", "progression"):
            avg_pace = int(((pace_min or 0) + (pace_max or 0)) / 2) if pace_min and pace_max else None
            target = make_target(avg_pace)
            steps = [
                create_warmup_step(1000.0, step_order=1),
                create_interval_step(float(max(distance_m - 2000, 1000)), step_order=2, target_type=target),
                create_cooldown_step(1000.0, step_order=3),
            ]
        elif wtype == "intervals":
            steps = [
                create_warmup_step(1600.0, step_order=1),
                create_interval_step(float(max(distance_m - 3200, 400)), step_order=2),
                create_cooldown_step(1600.0, step_order=3),
            ]
        else:
            steps = [create_interval_step(float(distance_m or 5000), step_order=1)]

    segment = WorkoutSegment(
        segmentOrder=1,
        sportType={"sportTypeId": 1, "sportTypeKey": "running"},
        workoutSteps=steps,
    )
    est_duration = int((distance_m / 1000) * (pace_min or 360)) if distance_m else 3600
    return RunningWorkout(
        workoutName=name,
        estimatedDurationInSecs=est_duration,
        workoutSegments=[segment],
    )

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/connect", methods=["POST"])
def connect():
    body = request.get_json()
    email = body.get("email")
    password = body.get("password")
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    try:
        from garminconnect import Garmin
        token_dir = pathlib.Path(tempfile.mkdtemp())
        client = Garmin(email=email, password=password, prompt_mfa=None)
        client.login(str(token_dir))
        token_file = token_dir / "garmin_tokens.json"
        tokens = json.loads(token_file.read_text()) if token_file.exists() else {}
        try:
            profile = client.get_full_name() or email
        except Exception:
            profile = email
        existing = supabase_request("GET", "user_integrations?provider=eq.garmin")
        if existing:
            supabase_request("PATCH", "user_integrations?provider=eq.garmin", {
                "garmin_email": email,
                "garmin_tokens": tokens,
                "garmin_athlete_name": profile,
            })
        else:
            supabase_request("POST", "user_integrations", {
                "provider": "garmin",
                "garmin_email": email,
                "garmin_tokens": tokens,
                "garmin_athlete_name": profile,
            })
        return jsonify({"ok": True, "name": profile})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/push-workout", methods=["POST"])
def push_workout():
    body = request.get_json()
    workout = body.get("workout")
    date_str = body.get("date")
    if not workout or not date_str:
        return jsonify({"error": "workout and date required"}), 400
    try:
        email, tokens = get_garmin_tokens()
        if not email or not tokens:
            return jsonify({"error": "Garmin not connected"}), 401
        client, token_dir = get_garmin_client(email, tokens)
        old_garmin_id = workout.get("garmin_workout_id")
        if old_garmin_id:
            try:
                client.delete_workout(old_garmin_id)
            except Exception:
                pass
        gw = build_garmin_workout(workout)
        result = client.upload_running_workout(gw)
        garmin_id = result.get("workoutId")
        client.schedule_workout(garmin_id, date_str)
        save_garmin_tokens(token_dir)
        supabase_request("PATCH", f"workouts?id=eq.{workout['id']}", {
            "garmin_workout_id": garmin_id
        })
        return jsonify({"ok": True, "garmin_workout_id": garmin_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/push-week", methods=["POST"])
def push_week():
    body = request.get_json()
    workouts = body.get("workouts", [])
    try:
        email, tokens = get_garmin_tokens()
        if not email or not tokens:
            return jsonify({"error": "Garmin not connected"}), 401
        client, token_dir = get_garmin_client(email, tokens)
        results = []
        for workout in workouts:
            if workout.get("type") == "rest":
                continue
            date_str = workout.get("scheduled_date")
            if not date_str:
                continue
            try:
                old_garmin_id = workout.get("garmin_workout_id")
                if old_garmin_id:
                    try:
                        client.delete_workout(old_garmin_id)
                    except Exception:
                        pass
                gw = build_garmin_workout(workout)
                result = client.upload_running_workout(gw)
                garmin_id = result.get("workoutId")
                client.schedule_workout(garmin_id, date_str)
                supabase_request("PATCH", f"workouts?id=eq.{workout['id']}", {
                    "garmin_workout_id": garmin_id
                })
                results.append({"id": workout["id"], "ok": True, "garmin_workout_id": garmin_id})
            except Exception as e:
                results.append({"id": workout["id"], "ok": False, "error": str(e)})
        save_garmin_tokens(token_dir)
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)