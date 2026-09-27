import sys
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-ba8a671d1983"

SAMPLE_EXERCISES = [
    {
        "id": "bench-press",
        "name": "Barbell Bench Press",
        "category": "Strength",
        "muscle_group": "Chest",
        "equipment": "Barbell",
        "recommended_reps": "8-12 reps x 4 sets",
        "description": "Compound upper body push exercise targeting the chest, shoulders, and triceps."
    },
    {
        "id": "barbell-squat",
        "name": "Barbell Back Squat",
        "category": "Strength",
        "muscle_group": "Legs",
        "equipment": "Barbell",
        "recommended_reps": "6-10 reps x 4 sets",
        "description": "Fundamental lower body exercise strengthening quads, glutes, and core."
    },
    {
        "id": "deadlift",
        "name": "Conventional Deadlift",
        "category": "Strength",
        "muscle_group": "Back",
        "equipment": "Barbell",
        "recommended_reps": "5 reps x 3 sets",
        "description": "Posterior chain compound exercise building back, hamstrings, and grip strength."
    },
    {
        "id": "pull-ups",
        "name": "Bodyweight Pull-ups",
        "category": "Calisthenics",
        "muscle_group": "Back",
        "equipment": "Pull-up Bar",
        "recommended_reps": "8-15 reps x 3 sets",
        "description": "Bodyweight upper body pull exercise for latissimus dorsi and biceps."
    },
    {
        "id": "overhead-press",
        "name": "Overhead Barbell Press",
        "category": "Strength",
        "muscle_group": "Shoulders",
        "equipment": "Barbell",
        "recommended_reps": "8-10 reps x 3 sets",
        "description": "Vertical pushing exercise strengthening deltoids and upper triceps."
    }
]

def seed():
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection("exercises")
    print(f"Seeding exercises collection in Firestore project '{PROJECT_ID}'...")
    
    for item in SAMPLE_EXERCISES:
        doc_id = item["id"]
        doc_ref = collection_ref.document(doc_id)
        doc_ref.set(item)
        print(f"  ✓ Seeded: {item['name']} ({doc_id})")
        
    print("Firestore seeding complete!")

if __name__ == "__main__":
    seed()
