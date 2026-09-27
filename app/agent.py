# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from app.a2ui_utils import a2ui_callback

# Hardcoded GCP Project ID and GCS Bucket Name as specified in requirements
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-03-ba8a671d1983"
GCS_BUCKET_NAME = "fitcoach-ai-media-qwiklabs-gcp-03"
MEMORY_BANK_ID = "4382695129756991488"

# Load Agent Engine / Sandbox resource name from deployment_metadata.json
DEPLOYMENT_METADATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deployment_metadata.json")
agent_engine_resource_name = None
sandbox_resource_name = None

if os.path.exists(DEPLOYMENT_METADATA_PATH):
    try:
        with open(DEPLOYMENT_METADATA_PATH, "r") as f:
            metadata = json.load(f)
            agent_engine_resource_name = metadata.get("remote_agent_runtime_id")
            sandbox_resource_name = metadata.get("sandbox_resource_name")
            if agent_engine_resource_name:
                MEMORY_BANK_ID = agent_engine_resource_name.split("/")[-1]
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    sandbox_resource_name=sandbox_resource_name,
    agent_engine_resource_name=agent_engine_resource_name
)

async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: After each agent turn, extract and send durable facts to Memory Bank."""
    await callback_context.add_session_to_memory()
    return None

def memory_service_builder():
    """Builds VertexAiMemoryBankService pointing to the Agent Engine Memory Bank."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )

def get_firestore_client():
    """Returns a Firestore client initialized with hardcoded project ID."""
    return firestore.Client(project=FIRESTORE_PROJECT_ID)

def generate_exercise_visual(
    exercise_name: str,
    prompt_description: str,
    tool_context: ToolContext
) -> str:
    """Generates an illustrative exercise form visual guide using gemini-3.1-flash-lite-image model in the global region.
    Saves the image with tool_context.save_artifact so it shows up in Playground Artifacts and uploads image bytes to public GCS bucket.

    Args:
        exercise_name: Name of the exercise or fitness item (e.g. 'barbell-squat', 'deadlift', 'kettlebell-swing').
        prompt_description: Visual instructions describing the exercise pose, muscle emphasis, or form guide.

    Returns:
        The public HTTPS URL of the uploaded image in Cloud Storage (https://storage.googleapis.com/<bucket>/<object>).
    """
    client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
    prompt = f"Professional fitness illustration showing proper form for {exercise_name}. {prompt_description}"
    
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
        ),
    )
    
    image_bytes = None
    mime_type = "image/png"
    if response.candidates and response.candidates[0].content:
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                image_bytes = part.inline_data.data
                mime_type = part.inline_data.mime_type or "image/png"
                break
            
    if not image_bytes:
        return f"Failed to generate image for exercise '{exercise_name}'."
        
    slug = exercise_name.strip().lower().replace(" ", "-")
    ext = "jpg" if "jpeg" in mime_type else "png"
    filename = f"{slug}-visual.{ext}"
    
    # 1. Save artifact using tool_context.save_artifact
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    tool_context.save_artifact(filename=filename, artifact=image_part)
    
    # 2. Upload image bytes directly to public GCS bucket
    storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob_name = f"exercise_visuals/{filename}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(image_bytes, content_type=mime_type)
    
    public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{blob_name}"
    return (
        f"Generated exercise form visual for '{exercise_name}'.\n"
        f"Saved artifact: {filename}\n"
        f"Public Storage URL: {public_url}"
    )

def geocode_address(address: str) -> str:
    """Converts a street address or city name into geographic coordinates (latitude, longitude) using Google Maps Geocoding API.

    Args:
        address: The street address, landmark, or city name to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA' or 'San Francisco, CA').

    Returns:
        Formatted location details including formatted address, latitude, and longitude.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FitCoach-AI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") != "OK" or not data.get("results"):
                return f"Geocoding failed for address '{address}': {data.get('status')}"
            
            result = data["results"][0]
            fmt_address = result.get("formatted_address", address)
            loc = result["geometry"]["location"]
            lat = loc["lat"]
            lng = loc["lng"]
            
            return f"Geocoding Result:\n• Address: {fmt_address}\n• Latitude: {lat}\n• Longitude: {lng}"
    except Exception as e:
        return f"Error executing Geocoding request: {str(e)}"

def find_nearby_places(latitude: float, longitude: float, place_type: str = "gym", radius_meters: float = 2000.0) -> str:
    """Finds nearby places of a given type around coordinates using Google Maps Places API (New).

    Args:
        latitude: Latitude of the center point (e.g., 37.7749).
        longitude: Longitude of the center point (e.g., -122.4194).
        place_type: Type of places to search for (e.g. 'gym', 'park', 'fitness_center', 'health', 'sports_complex').
        radius_meters: Search radius in meters (default: 2000.0).

    Returns:
        Formatted summary of nearby places showing name, address, and location coordinates.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    url = "https://places.googleapis.com/v1/places:searchNearby"
    payload = {
        "includedTypes": [place_type.strip().lower()],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude
                },
                "radius": float(radius_meters)
            }
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            places = data.get("places", [])
            
            if not places:
                return f"No nearby places of type '{place_type}' found within {radius_meters}m of ({latitude}, {longitude})."
            
            output = []
            for p in places:
                name = p.get("displayName", {}).get("text", "Unnamed Place")
                addr = p.get("formattedAddress", "N/A")
                loc = p.get("location", {})
                lat = loc.get("latitude", "N/A")
                lng = loc.get("longitude", "N/A")
                output.append(f"• Name: {name}\n  Address: {addr}\n  Location: ({lat}, {lng})")
            
            return f"Nearby '{place_type}' Search Results ({len(output)} found):\n" + "\n\n".join(output)
    except Exception as e:
        return f"Error executing Places API (New) request: {str(e)}"

def fetch_public_exercise_catalog(limit: int = 5) -> str:
    """Fetches real exercise data from the free public WGER Exercise Manager API (https://wger.de/api/v2/exerciseinfo/).

    Args:
        limit: Number of public exercises to retrieve (1 to 10).

    Returns:
        Formatted exercise names, categories, equipment, and descriptions from WGER API.
    """
    api_key = os.getenv("WGER_API_KEY", "")
    fetch_limit = max(1, min(limit, 10))
    url = f"https://wger.de/api/v2/exerciseinfo/?limit={fetch_limit}"
    
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Token {api_key}")
    req.add_header("User-Agent", "FitCoach-AI/1.0")
    
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            
            output = []
            for item in results:
                translations = item.get("translations", [])
                name = "Unknown Exercise"
                desc = "No description available."
                for t in translations:
                    if t.get("language") == 2 and t.get("name"):  # English translation
                        name = t.get("name")
                        desc = (t.get("description_source") or t.get("description") or "")[:150]
                        break
                if name == "Unknown Exercise" and translations:
                    name = translations[0].get("name", name)
                
                category = item.get("category", {}).get("name", "General")
                equipment = ", ".join([e.get("name", "") for e in item.get("equipment", [])]) or "None"
                output.append(f"• {name} | Category: {category} | Equipment: {equipment}\n  Summary: {desc.strip()}")
            
            return f"WGER Public Exercise Catalog ({len(output)} retrieved):\n" + "\n".join(output)
    except Exception as e:
        return f"Error fetching from WGER Public API: {str(e)}"

def calculate_1rm(weight_lbs: float, reps: int) -> str:
    """Calculates estimated 1-Rep Max (1RM) and recommended working weight percentages based on weight lifted and reps completed.

    Args:
        weight_lbs: The weight lifted in pounds (e.g. 185.0 or 225.0). Must be greater than 0.
        reps: The number of completed repetitions (e.g. 1 to 12). Must be at least 1.

    Returns:
        A structured breakdown of the estimated 1RM (using Epley & Brzycki formulas) and training intensity zones (70% to 90%).
    """
    if weight_lbs <= 0 or reps < 1:
        return "Please provide a positive weight and at least 1 rep."
    
    if reps == 1:
        epley_1rm = weight_lbs
        brzycki_1rm = weight_lbs
    else:
        epley_1rm = weight_lbs * (1.0 + reps / 30.0)
        brzycki_1rm = weight_lbs * (36.0 / (37.0 - reps)) if reps < 37 else epley_1rm
    
    avg_1rm = (epley_1rm + brzycki_1rm) / 2.0
    
    zones = {
        "90% (Heavy / Strength Focus)": round(avg_1rm * 0.90, 1),
        "85% (Strength / Hypertrophy)": round(avg_1rm * 0.85, 1),
        "80% (Working Sets Baseline)": round(avg_1rm * 0.80, 1),
        "75% (Hypertrophy / Volume)": round(avg_1rm * 0.75, 1),
        "70% (Endurance / Warmup)": round(avg_1rm * 0.70, 1),
    }
    
    zones_str = "\n".join([f"  • {pct}: {wt} lbs" for pct, wt in zones.items()])
    
    return (
        f"1-Rep Max (1RM) Calculation Results for {weight_lbs} lbs x {reps} reps:\n"
        f"• Estimated 1RM (Epley Formula): {round(epley_1rm, 1)} lbs\n"
        f"• Estimated 1RM (Brzycki Formula): {round(brzycki_1rm, 1)} lbs\n"
        f"• Recommended Average 1RM: {round(avg_1rm, 1)} lbs\n\n"
        f"Training Weight Intensity Zones:\n{zones_str}"
    )

def log_workout_session(exercise_id: str, sets: int, reps: int, weight_lbs: float) -> str:
    """Logs a completed workout session to Firestore.

    Args:
        exercise_id: ID or name of the exercise performed (e.g. 'bench-press', 'barbell-squat').
        sets: Number of sets completed.
        reps: Repetitions per set.
        weight_lbs: Weight lifted in pounds.

    Returns:
        Confirmation message with calculated total volume logged.
    """
    db = get_firestore_client()
    volume = sets * reps * weight_lbs
    doc_ref = db.collection("workout_logs").document()
    doc_ref.set({
        "exercise_id": exercise_id,
        "sets": sets,
        "reps": reps,
        "weight_lbs": weight_lbs,
        "total_volume_lbs": volume,
        "logged_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    })
    return f"Logged workout: {sets} sets x {reps} reps @ {weight_lbs} lbs on '{exercise_id}' (Total Volume: {volume:,.1f} lbs)."

def list_exercises(muscle_group: str = "") -> str:
    """Lists exercises stored in the Firestore database, optionally filtered by target muscle group.

    Args:
        muscle_group: Optional string to filter exercises by target muscle group (e.g., 'Chest', 'Legs', 'Back', 'Shoulders').

    Returns:
        A list of exercise summaries matching the filter.
    """
    db = get_firestore_client()
    collection_ref = db.collection("exercises")
    docs = collection_ref.stream()
    
    results = []
    for doc in docs:
        data = doc.to_dict()
        if muscle_group and muscle_group.lower() not in data.get("muscle_group", "").lower():
            continue
        results.append(
            f"• [{data.get('id')}] {data.get('name')} | Muscle: {data.get('muscle_group')} | Equipment: {data.get('equipment')} | Reps: {data.get('recommended_reps')}"
        )
    
    if not results:
        return f"No exercises found matching muscle group '{muscle_group}'."
    
    return "Available Exercises:\n" + "\n".join(results)

def get_exercise_details(exercise_id: str) -> str:
    """Retrieves full details of a specific exercise from Firestore by exercise ID or name.

    Args:
        exercise_id: The ID or slug of the exercise (e.g., 'bench-press', 'barbell-squat', 'deadlift', 'pull-ups', 'overhead-press').

    Returns:
        Full details of the requested exercise document.
    """
    db = get_firestore_client()
    doc_ref = db.collection("exercises").document(exercise_id.strip().lower())
    doc = doc_ref.get()
    
    if not doc.exists:
        # Fallback search by name if exact ID not found
        docs = db.collection("exercises").stream()
        for d in docs:
            data = d.to_dict()
            if exercise_id.lower() in data.get("name", "").lower() or exercise_id.lower() in data.get("id", "").lower():
                return (
                    f"Exercise: {data.get('name')} ({data.get('id')})\n"
                    f"Category: {data.get('category')}\n"
                    f"Target Muscle Group: {data.get('muscle_group')}\n"
                    f"Equipment: {data.get('equipment')}\n"
                    f"Recommended Sets/Reps: {data.get('recommended_reps')}\n"
                    f"Description: {data.get('description')}"
                )
        return f"Exercise '{exercise_id}' not found in database."
        
    data = doc.to_dict()
    return (
        f"Exercise: {data.get('name')} ({data.get('id')})\n"
        f"Category: {data.get('category')}\n"
        f"Target Muscle Group: {data.get('muscle_group')}\n"
        f"Equipment: {data.get('equipment')}\n"
        f"Recommended Sets/Reps: {data.get('recommended_reps')}\n"
        f"Description: {data.get('description')}"
    )

def add_exercise(
    exercise_id: str,
    name: str,
    category: str,
    muscle_group: str,
    equipment: str,
    recommended_reps: str,
    description: str
) -> str:
    """Adds a new exercise or updates an existing exercise in the Firestore database.

    Args:
        exercise_id: Unique identifier for the exercise (e.g. 'dumbbell-curl', 'dips').
        name: Display name of the exercise (e.g. 'Dumbbell Bicep Curl').
        category: Exercise category (e.g., 'Strength', 'Calisthenics', 'Cardio').
        muscle_group: Primary muscle group targeted (e.g., 'Biceps', 'Triceps', 'Legs').
        equipment: Equipment required (e.g., 'Dumbbell', 'Machine', 'Bodyweight').
        recommended_reps: Recommended sets and rep count (e.g., '10-12 reps x 3 sets').
        description: Instructions or notes describing how to perform the movement.

    Returns:
        A confirmation message indicating successful addition/update.
    """
    db = get_firestore_client()
    slug_id = exercise_id.strip().lower().replace(" ", "-")
    doc_ref = db.collection("exercises").document(slug_id)
    
    exercise_data = {
        "id": slug_id,
        "name": name,
        "category": category,
        "muscle_group": muscle_group,
        "equipment": equipment,
        "recommended_reps": recommended_reps,
        "description": description
    }
    
    doc_ref.set(exercise_data)
    return f"Successfully saved exercise '{name}' ({slug_id}) to Firestore!"

def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"

# Build A2UI System Prompt using A2uiSchemaManager version 0.8 and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "FitCoach AI, a knowledgeable and encouraging fitness & workout coach. "
        "You help users plan workout routines, look up exercises in the local Firestore database or "
        "fetch exercises from the public WGER API, record new exercise entries, log workout sessions, "
        "calculate estimated 1-rep max (1RM) metrics, geocode addresses, find nearby gyms, generate exercise "
        "form guide visuals using gemini-3.1-flash-lite-image, and run custom Python data/fitness calculations "
        "in an isolated Agent Engine sandbox. Always provide clear, actionable fitness guidance and remember "
        "user preferences and fitness history across sessions."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        generate_exercise_visual,
        geocode_address,
        find_nearby_places,
        fetch_public_exercise_catalog,
        calculate_1rm,
        log_workout_session,
        list_exercises,
        get_exercise_details,
        add_exercise,
        get_weather,
        get_current_time
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
