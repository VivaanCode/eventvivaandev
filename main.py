from flask import Flask, render_template, redirect, request, jsonify, session, url_for
import os
import json
import psycopg2
import uuid
from datetime import datetime


try: 
  from dotenv import load_dotenv
  load_dotenv()
except:
  pass

db_url = os.getenv("DATABASE_URL")
ADMIN_CODE = os.getenv("ADMIN_CODE")
app = Flask('app', static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-here")

# Database connection helper
def get_db_connection():
    if not db_url:
        return None
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

# Initialize database tables
def init_db():
    conn = get_db_connection()
    if not conn:
        print("Warning: Database not available. Running without database.")
        return False
    
    try:
        cur = conn.cursor()
        
        # Create events table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                uuid VARCHAR(36) UNIQUE NOT NULL,
                passcode VARCHAR(50) NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                date DATE,
                time VARCHAR(10),
                location VARCHAR(255),
                address TEXT,
                capacity INTEGER,
                registered INTEGER DEFAULT 0,
                price DECIMAL(10,2),
                organizer VARCHAR(255),
                tags JSONB,
                image TEXT,
                date_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create RSVPs table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rsvps (
                id SERIAL PRIMARY KEY,
                event_uuid VARCHAR(36) NOT NULL,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                phone VARCHAR(20),
                additional_info TEXT,
                rsvp_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (event_uuid) REFERENCES events(uuid)
            )
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Database initialization error: {e}")
        return False

# Load event from database
def load_event(event_id):
    conn = get_db_connection()
    if not conn:
        # Fallback to hardcoded events if database is not available
        if event_id == 1:
            return {
                'id': 1,
                'uuid': '550e8400-e29b-41d4-a716-446655440001',
                'passcode': 'TECH2024',
                'title': 'Tech Conference 2024',
                'description': 'Annual technology conference featuring the latest innovations in AI, cloud computing, and software development.',
                'date': '2024-03-15',
                'time': '09:00 AM',
                'location': 'San Francisco Convention Center',
                'address': '747 Howard St, San Francisco, CA 94103',
                'capacity': 500,
                'registered': 234,
                'price': 420,
                'organizer': 'TechEvents Inc.',
                'tags': ['technology', 'AI', 'cloud', 'networking'],
                'image': 'https://picsum.photos/seed/techconf2024/800/400.jpg'
            }
        elif event_id == 2:
            return {
                'id': 2,
                'uuid': '550e8400-e29b-41d4-a716-446655440002',
                'passcode': 'MUSIC2024',
                'title': 'Summer Music Festival',
                'description': 'Three-day outdoor music festival featuring top artists from around the world. Food trucks, art installations, and camping available.',
                'date': '2024-07-20',
                'time': '12:00 PM',
                'location': 'Golden Gate Park',
                'address': '501 Stanyan St, San Francisco, CA 94117',
                'capacity': 10000,
                'registered': 7856,
                'price': 89,
                'organizer': 'Bay Area Music Productions',
                'tags': ['music', 'festival', 'outdoor', 'summer'],
                'image': 'https://picsum.photos/seed/musicfest2024/800/400.jpg'
            }
        return None
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
        event = cur.fetchone()
        cur.close()
        conn.close()
        
        if event:
            return {
                'id': event[0],
                'uuid': event[1],
                'passcode': event[2],
                'title': event[3],
                'description': event[4],
                'date': event[5].isoformat() if event[5] else None,
                'time': event[6],
                'location': event[7],
                'address': event[8],
                'capacity': event[9],
                'registered': event[10],
                'price': float(event[11]) if event[11] else 0,
                'organizer': event[12],
                'tags': event[13] if isinstance(event[13], list) else [],
                'image': event[14],
                'date_created': event[15].isoformat() if event[15] else None
            }
        return None
    except Exception as e:
        print(f"Error loading event {event_id}: {e}")
        return None

# Save event to database
def save_event_to_db(event_data):
    conn = get_db_connection()
    if not conn:
        print("Warning: Cannot save to database - connection not available")
        return False
    
    try:
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO events (uuid, passcode, title, description, date, time, location, address, 
                             capacity, registered, price, organizer, tags, image)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (uuid) DO UPDATE SET
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                date = EXCLUDED.date,
                time = EXCLUDED.time,
                location = EXCLUDED.location,
                address = EXCLUDED.address,
                capacity = EXCLUDED.capacity,
                registered = EXCLUDED.registered,
                price = EXCLUDED.price,
                organizer = EXCLUDED.organizer,
                tags = EXCLUDED.tags,
                image = EXCLUDED.image
        """, (
            event_data['uuid'], event_data['passcode'], event_data['title'],
            event_data['description'], event_data.get('date'), event_data.get('time'),
            event_data['location'], event_data['address'], event_data['capacity'],
            event_data['registered'], event_data['price'], event_data['organizer'],
            json.dumps(event_data['tags']), event_data['image']
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving event to database: {e}")
        return False

# Get event from database
def get_event_from_db(event_uuid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM events WHERE uuid = %s", (event_uuid,))
    event = cur.fetchone()
    cur.close()
    conn.close()
    
    if event:
        return {
            'id': event[0],
            'uuid': event[1],
            'passcode': event[2],
            'title': event[3],
            'description': event[4],
            'date': event[5].isoformat() if event[5] else None,
            'time': event[6],
            'location': event[7],
            'address': event[8],
            'capacity': event[9],
            'registered': event[10],
            'price': float(event[11]) if event[11] else 0,
            'organizer': event[12],
            'tags': event[13],
            'image': event[14],
            'date_created': event[15].isoformat() if event[15] else None
        }
    return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/event/<int:event_id>")
def event_page(event_id):
    """Display event page with password protection"""
    event = load_event(event_id)
    if not event:
        return "Event not found", 404
    
    # Check if password is provided in URL parameter
    password = request.args.get('password')
    if password and password == event['passcode']:
        session[f'event_{event_id}_authenticated'] = True
        save_event_to_db(event)  # Save to database when accessed with correct password
        return render_template("event.html", event=event)
    
    # Check if already authenticated in session
    if session.get(f'event_{event_id}_authenticated'):
        return render_template("event.html", event=event)
    
    # Show password entry form
    return render_template("password_entry.html", event_id=event_id, event_title=event['title'])

@app.route("/event/<int:event_id>/authenticate", methods=["POST"])
def authenticate_event(event_id):
    """Handle event password authentication"""
    event = load_event(event_id)
    if not event:
        return "Event not found", 404
    
    password = request.form.get('password')
    if password == event['passcode']:
        session[f'event_{event_id}_authenticated'] = True
        save_event_to_db(event)  # Save to database when authenticated
        return redirect(url_for('event_page', event_id=event_id))
    else:
        return render_template("password_entry.html", event_id=event_id, event_title=event['title'], error="Invalid password")

@app.route("/event/<int:event_id>/rsvp", methods=["POST"])
def rsvp_event(event_id):
    """Handle RSVP submission"""
    if not session.get(f'event_{event_id}_authenticated'):
        return redirect(url_for('event_page', event_id=event_id))
    
    event = load_event(event_id)
    if not event:
        return "Event not found", 404
    
    # Get RSVP data
    rsvp_data = {
        'name': request.form.get('name'),
        'email': request.form.get('email'),
        'phone': request.form.get('phone'),
        'additional_info': request.form.get('additional_info', '')
    }
    
    # Validate required fields
    if not rsvp_data['name'] or not rsvp_data['email']:
        return render_template("event.html", event=event, rsvp_error="Name and email are required")
    
    # Save RSVP to database if available
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO rsvps (event_uuid, name, email, phone, additional_info)
                VALUES (%s, %s, %s, %s, %s)
            """, (event['uuid'], rsvp_data['name'], rsvp_data['email'], rsvp_data['phone'], rsvp_data['additional_info']))
            conn.commit()
            cur.close()
            conn.close()
            
            # Update registered count in database
            event['registered'] += 1
            save_event_to_db(event)
        except Exception as e:
            print(f"Error saving RSVP to database: {e}")
    else:
        print("RSVP data received but database not available")
        # Still increment registered count for display
        event['registered'] += 1
    
    return render_template("event.html", event=event, rsvp_success=True)

@app.route("/admin/create-event", methods=["GET", "POST"])
def create_event():
    """Admin endpoint to create new events"""
    # Check admin code
    if request.method == "GET":
        return render_template("create_event.html")
    
    admin_code = request.form.get('admin_code')
    if admin_code != ADMIN_CODE:
        return render_template("create_event.html", error="Invalid admin code")
    
    # Create new event
    event_data = {
        'uuid': str(uuid.uuid4()),
        'passcode': request.form.get('passcode'),
        'title': request.form.get('title'),
        'description': request.form.get('description'),
        'date': request.form.get('date'),
        'time': request.form.get('time'),
        'location': request.form.get('location'),
        'address': request.form.get('address'),
        'capacity': int(request.form.get('capacity', 0)),
        'registered': 0,
        'price': float(request.form.get('price', 0)),
        'organizer': request.form.get('organizer'),
        'tags': request.form.get('tags', '').split(',') if request.form.get('tags') else [],
        'image': request.form.get('image', '')
    }
    
    # Save to database if available
    if save_event_to_db(event_data):
        return render_template("create_event.html", success=f"Event created successfully! UUID: {event_data['uuid']}")
    else:
        return render_template("create_event.html", error="Event created but database not available. UUID: {event_data['uuid']}")

# Commented out all API routes as requested
# # API Routes for Events
# @app.route("/api/events", methods=["GET"])
# def get_events():
#     """Get all events"""
#     try:
#         conn = get_db_connection()
#         cur = conn.cursor()
#         cur.execute("SELECT * FROM events ORDER BY date_created DESC")
#         events = cur.fetchall()
#         cur.close()
#         conn.close()
        
#         # Convert to list of dicts
#         event_list = []
#         for event in events:
#             event_list.append({
#                 "id": event[0],
#                 "title": event[1],
#                 "description": event[2],
#                 "date": event[3].isoformat() if event[3] else None,
#                 "location": event[4],
#                 "date_created": event[5].isoformat() if event[5] else None
#             })
        
#         return jsonify({"events": event_list})
#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

# @app.route("/api/events/<int:event_id>", methods=["GET"])
# def get_event(event_id):
#     """Get a specific event by ID"""
#     try:
#         conn = get_db_connection()
#         cur = conn.cursor()
#         cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
#         event = cur.fetchone()
#         cur.close()
#         conn.close()
        
#         if not event:
#             return jsonify({"error": "Event not found"}), 404
        
#         return jsonify({
#             "id": event[0],
#             "title": event[1],
#             "description": event[2],
#             "date": event[3].isoformat() if event[3] else None,
#             "location": event[4],
#             "date_created": event[5].isoformat() if event[5] else None
#         })
#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

# # Commented out create event route as requested
# # @app.route("/api/events", methods=["POST"])
# # def create_event():
# #     """Create a new event"""
# #     try:
# #         data = request.get_json()
# #         
# #         if not data or not data.get("title"):
# #             return jsonify({"error": "Title is required"}), 400
# #         
# #         conn = get_db_connection()
# #         cur = conn.cursor()
# #         cur.execute(
# #             """
# #             INSERT INTO events (title, description, date, location, date_created)
# #             VALUES (%s, %s, %s, %s, %s)
# #             RETURNING id
# #             """,
# #             (
# #                 data.get("title"),
# #                 data.get("description", ""),
# #                 datetime.fromisoformat(data["date"]).date() if data.get("date") else None,
# #                 data.get("location", ""),
# #                 datetime.now()
# #             )
# #         )
# #         event_id = cur.fetchone()[0]
# #         conn.commit()
# #         cur.close()
# #         conn.close()
# #         
# #         return jsonify({"id": event_id, "message": "Event created successfully"}), 201
# #     except Exception as e:
# #         return jsonify({"error": str(e)}), 500

# @app.route("/api/events/<int:event_id>", methods=["PUT"])
# def update_event(event_id):
#     """Update an existing event"""
#     try:
#         data = request.get_json()
#         
#         if not data:
#             return jsonify({"error": "No data provided"}), 400
#         
#         conn = get_db_connection()
#         cur = conn.cursor()
#         
#         # Check if event exists
#         cur.execute("SELECT id FROM events WHERE id = %s", (event_id,))
#         if not cur.fetchone():
#             cur.close()
#             conn.close()
#             return jsonify({"error": "Event not found"}), 404
#         
#         # Build dynamic update query
#         update_fields = []
#         values = []
#         
#         if "title" in data:
#             update_fields.append("title = %s")
#             values.append(data["title"])
#         if "description" in data:
#             update_fields.append("description = %s")
#             values.append(data["description"])
#         if "date" in data:
#             update_fields.append("date = %s")
#             values.append(datetime.fromisoformat(data["date"]).date() if data["date"] else None)
#         if "location" in data:
#             update_fields.append("location = %s")
#             values.append(data["location"])
#         
#         if not update_fields:
#             cur.close()
#             conn.close()
#             return jsonify({"error": "No valid fields to update"}), 400
#         
#         values.append(event_id)
#         query = f"UPDATE events SET {', '.join(update_fields)} WHERE id = %s"
#         
#         cur.execute(query, values)
#         conn.commit()
#         cur.close()
#         conn.close()
#         
#         return jsonify({"message": "Event updated successfully"})
#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

# @app.route("/api/events/<int:event_id>", methods=["DELETE"])
# def delete_event(event_id):
#     """Delete an event"""
#     try:
#         conn = get_db_connection()
#         cur = conn.cursor()
#         
#         # Check if event exists
#         cur.execute("SELECT id FROM events WHERE id = %s", (event_id,))
#         if not cur.fetchone():
#             cur.close()
#             conn.close()
#             return jsonify({"error": "Event not found"}), 404
#         
#         cur.execute("DELETE FROM events WHERE id = %s", (event_id,))
#         conn.commit()
#         cur.close()
#         conn.close()
#         
#         return jsonify({"message": "Event deleted successfully"})
#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

# Actually organizing my code this time lol

# API Routes
@app.route("/api/signup", methods=["POST"])
def signup():
    user = request.json
    return jsonify(user)

if __name__ == '__main__':
    # Initialize database tables if available
    if db_url:
        print("Initializing database...")
        if init_db():
            print("Database initialized successfully")
        else:
            print("Database initialization failed, running without database")
    else:
        print("No DATABASE_URL provided, running without database")
    
    app.run(host='0.0.0.0', port=8080)
