from flask import Flask, render_template, redirect, request, jsonify, session, url_for
import resend
from werkzeug.security import generate_password_hash, check_password_hash
import os
import json
import psycopg2
import uuid
from datetime import datetime, timedelta
import re
import time

try: 
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

db_url = os.getenv("DATABASE_URL")
resend.api_key = os.getenv("RESEND_API_KEY")
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

# Validate URL helper
def isValidUrl(url):
    try:
        from urllib.parse import urlparse
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

# Email validation helper
def isValidEmail(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

# Rate limiting helper
def is_rate_limited(key, max_requests=5, window_seconds=300):
    """Simple in-memory rate limiting"""
    if not hasattr(is_rate_limited, 'requests'):
        is_rate_limited.requests = {}
    
    now = time.time()
    if key not in is_rate_limited.requests:
        is_rate_limited.requests[key] = []
    
    # Clean old requests
    is_rate_limited.requests[key] = [
        req_time for req_time in is_rate_limited.requests[key] 
        if now - req_time < window_seconds
    ]
    
    # Check if over limit
    if len(is_rate_limited.requests[key]) >= max_requests:
        return True
    
    # Add current request
    is_rate_limited.requests[key].append(now)
    return False

# Initialize database tables
def init_db():
    conn = get_db_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        # Events table with segment_id field
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                uuid VARCHAR(36) UNIQUE NOT NULL,
                passcode_hash VARCHAR(255),
                event_data JSONB NOT NULL,
                segment_id VARCHAR(36),
                date_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                date_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Updated RSVPs table with verification fields and segment_id
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rsvps (
                id SERIAL PRIMARY KEY,
                event_uuid VARCHAR(36) NOT NULL,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                phone VARCHAR(20),
                additional_info TEXT,
                email_verified BOOLEAN DEFAULT FALSE,
                verification_token VARCHAR(36),
                segment_id VARCHAR(36),
                segment_error TEXT,
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

def send_verification_email(email, name, token):
    verify_url = url_for('verify_email', token=token, _external=True)
    params = {
        "from": "verify@email.vivaan.dev", # Replace with your verified domain in production
        "to": email,
        "subject": "Verify your RSVP",
        "html": f"""
            <div style="font-family: sans-serif; padding: 20px; border: 1px solid #eee;">
                <h2>Hi {name}!</h2>
                <p>Thanks for RSVPing. Please click the button below to verify your email address:</p>
                <a href="{verify_url}" style="background: #e63946; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">Verify Email</a>
                <p style="margin-top: 20px; font-size: 0.8rem; color: #666;">If you didn't request this, you can ignore this email.</p>
            </div>
        """
    }
    try:
        resend.Emails.send(params)
        return True
    except Exception as e:
        print(f"Resend error: {e}")
        return False

def add_contact_to_segment(email, name, segment_id):
    """Add contact to Resend segment"""
    if not segment_id:
        return None, "No segment ID provided"
    
    try:
        # First create the contact
        contact_params = {
            "email": email,
            "first_name": name.split()[0] if name and ' ' in name else name,
            "last_name": name.split()[-1] if name and ' ' in name else "",
        }
        
        contact_response = resend.Contacts.create(contact_params)
        contact_id = contact_response.get('id')
        
        if not contact_id:
            return None, "Failed to create contact"
        
        # Then add to segment
        segment_params = {
            "segment_id": segment_id,
            "contact_id": contact_id,
        }
        
        response = resend.Contacts.Segments.add(segment_params)
        return True, None
        
    except Exception as e:
        error_msg = f"Failed to add contact to segment: {str(e)}"
        print(error_msg)
        return False, error_msg

# Load event from database
def load_event(event_uuid):
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT event_data FROM events WHERE uuid = %s", (event_uuid,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result:
            return json.loads(result[0]) if isinstance(result[0], str) else result[0]
        return None
    except Exception as e:
        print(f"Error loading event {event_uuid}: {e}")
        return None

# Get event with passcode verification
def get_event_from_db(event_uuid):
    conn = get_db_connection()
    if not conn:
        return None, None
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT event_data, passcode_hash, segment_id FROM events WHERE uuid = %s", (event_uuid,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result:
            event_data = json.loads(result[0]) if isinstance(result[0], str) else result[0]
            passcode_hash = result[1]
            segment_id = result[2]
            return event_data, passcode_hash, segment_id
        return None, None, None
    except Exception as e:
        print(f"Error loading event {event_uuid}: {e}")
        return None, None, None

# Check for duplicate RSVP by name, email, or phone
def check_rsvp_duplicate(event_uuid, name, email, phone):
    """
    Check if an RSVP already exists for this event
    Returns (is_duplicate, duplicate_fields, existing_rsvp)
    """
    conn = get_db_connection()
    if not conn:
        return False, [], None
    
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, email, phone FROM rsvps 
            WHERE event_uuid = %s
        """, (event_uuid,))
        existing_rsvps = cur.fetchall()
        cur.close()
        conn.close()
        
        duplicate_fields = []
        for rsvp in existing_rsvps:
            _, existing_name, existing_email, existing_phone = rsvp
            
            # Check for exact name match (case-insensitive)
            if name.lower().strip() == existing_name.lower().strip():
                return True, ['name'], rsvp
            
            # Check for email match
            if email and existing_email and email.lower().strip() == existing_email.lower().strip():
                duplicate_fields.append('email')
            
            # Check for phone match
            if phone and existing_phone and phone.strip() == existing_phone.strip():
                duplicate_fields.append('phone')
            
            if duplicate_fields:
                return False, duplicate_fields, rsvp
        
        return False, [], None
    except Exception as e:
        print(f"Error checking RSVP duplicate: {e}")
        return False, [], None

# Save event to database
def save_event_to_db(event_data, passcode=None, segment_id=None):
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        event_uuid = event_data.get('uuid')
        
        # Only hash the password if one was actually passed in
        passcode_hash = generate_password_hash(passcode) if passcode else None
        
        # Using COALESCE in the UPDATE ensures that if EXCLUDED.passcode_hash is NULL, 
        # it keeps the existing value in the table.
        cur.execute("""
            INSERT INTO events (uuid, passcode_hash, event_data, segment_id, date_updated)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (uuid) DO UPDATE SET
                passcode_hash = COALESCE(EXCLUDED.passcode_hash, events.passcode_hash),
                event_data = EXCLUDED.event_data,
                segment_id = COALESCE(EXCLUDED.segment_id, events.segment_id),
                date_updated = CURRENT_TIMESTAMP
            RETURNING uuid
        """, (event_uuid, passcode_hash, json.dumps(event_data), segment_id))
        
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return result[0] if result else None
    except Exception as e:
        print(f"Error saving event to database: {e}")
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/event/<event_uuid>")
def event_page(event_uuid):
    """Display event page with optional password protection"""
    event, passcode_hash, segment_id = get_event_from_db(event_uuid)
    if not event:
        return "Event not found", 404
    
    # Check if password is correct and provided
    session_key = f'event_{event_uuid}_authenticated'
    password = request.args.get('password')
    
    # If event has no password, always allow access
    if not passcode_hash:
        session[session_key] = True
    # If password is provided, verify it
    elif password:
        if check_password_hash(passcode_hash, password):
            session[session_key] = True
            return redirect(url_for('event_page', event_uuid=event_uuid))
        else:
            return render_template("password_entry.html", event_uuid=event_uuid, event_title=event.get('title', 'Event'), error="Invalid passcode")
    # Check if already authenticated in session
    elif not session.get(session_key):
        # Show password entry form
        return render_template("password_entry.html", event_uuid=event_uuid, event_title=event.get('title', 'Event'))
    
    # Get URL parameters for messages
    rsvp_success = request.args.get('rsvp_success')
    rsvp_error = request.args.get('rsvp_error')
    rsvp_warning = request.args.get('rsvp_warning')
    show_confirm = request.args.get('show_confirm')
    pending_rsvp = session.get(f'pending_rsvp_{event_uuid}')
    
    # Build error message based on error code
    error_message = None
    if rsvp_error:
        if rsvp_error == "required_fields":
            error_message = "Name and email are required"
        elif rsvp_error == "name_taken":
            error_message = "Someone with that name has already registered for this event. Please use a different name."
        elif rsvp_error == "save_failed":
            error_message = "Error saving RSVP. Please try again."
        elif rsvp_error == "invalid_email":
            error_message = "Please enter a valid email address."
        elif rsvp_error == "invalid_name":
            error_message = "Name must be between 2 and 100 characters."
        elif rsvp_error == "invalid_phone":
            error_message = "Please enter a valid phone number (10-20 characters)."
        else:
            error_message = rsvp_error
    
    # Build warning message based on warning flag and session data
    warning_message = None
    if rsvp_warning and pending_rsvp:
        warning_fields = []
        if 'email' in request.args.get('rsvp_fields', '').split(','):
            warning_fields.append(f"Email: {pending_rsvp.get('email')}")
        if 'phone' in request.args.get('rsvp_fields', '').split(','):
            warning_fields.append(f"Phone: {pending_rsvp.get('phone')}")
        
        # Check what fields are duplicated by looking at the actual data
        conn = get_db_connection()
        if conn:
            duplicate_fields = []
            cur = conn.cursor()
            cur.execute("""
                SELECT email, phone FROM rsvps 
                WHERE event_uuid = %s
            """, (event_uuid,))
            existing_rsvps = cur.fetchall()
            cur.close()
            conn.close()
            
            for existing_email, existing_phone in existing_rsvps:
                if pending_rsvp.get('email') and existing_email and pending_rsvp.get('email').lower() == existing_email.lower():
                    duplicate_fields.append(f"Email ({pending_rsvp.get('email')})")
                if pending_rsvp.get('phone') and existing_phone and pending_rsvp.get('phone') == existing_phone:
                    duplicate_fields.append(f"Phone ({pending_rsvp.get('phone')})")
            
            if duplicate_fields:
                warning_message = f"Warning: The following information is already registered: {', '.join(duplicate_fields)}. You can still register if you'd like."
    
    # Prepare template context
    context = {'event': event}
    if rsvp_success:
        context['rsvp_success'] = True
    if error_message:
        context['rsvp_error'] = error_message
    if warning_message and pending_rsvp:
        context['rsvp_warning'] = warning_message
        context['rsvp_warning_data'] = pending_rsvp
        context['show_confirm'] = True
    
    # Fetch actual RSVP count from DB instead of just the JSON counter
    conn = get_db_connection()
    rsvp_count = 0
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rsvps WHERE event_uuid = %s", (event_uuid,))
        rsvp_count = cur.fetchone()[0]
        cur.close()
        conn.close()

    # Pass rsvp_count to template
    return render_template("event.html", rsvp_count=rsvp_count, **context)

@app.route("/event/<event_uuid>/authenticate", methods=["POST"])
def authenticate_event(event_uuid):
    """Handle event password authentication"""
    event, passcode_hash, segment_id = get_event_from_db(event_uuid)
    if not event:
        return "Event not found", 404
    
    # If no passcode required, redirect directly
    if not passcode_hash:
        session[f'event_{event_uuid}_authenticated'] = True
        return redirect(url_for('event_page', event_uuid=event_uuid))
    
    password = request.form.get('password', '')
    if check_password_hash(passcode_hash, password):
        session[f'event_{event_uuid}_authenticated'] = True
        return redirect(url_for('event_page', event_uuid=event_uuid))
    else:
        return render_template("password_entry.html", event_uuid=event_uuid, event_title=event.get('title', 'Event'), error="Invalid passcode")

@app.route("/event/<event_uuid>/rsvp", methods=["POST"])
def rsvp_event(event_uuid):
    """Handle RSVP submission"""
    session_key = f'event_{event_uuid}_authenticated'
    if not session.get(session_key):
        return redirect(url_for('event_page', event_uuid=event_uuid))
    
    # Rate limiting per IP
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
    if is_rate_limited(f"rsvp_{client_ip}", max_requests=3, window_seconds=300):
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="Too many RSVP attempts. Please try again later."))
    
    event, _, segment_id = get_event_from_db(event_uuid)
    if not event:
        return "Event not found", 404
    
    # Get RSVP data
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    additional_info = request.form.get('additional_info', '').strip()
    
    # Validate required fields
    if not name or not email:
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="required_fields"))
    
    # Validate email format
    if not isValidEmail(email):
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="invalid_email"))
    
    # Validate name length and content
    if len(name) < 2 or len(name) > 100:
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="invalid_name"))
    
    # Validate phone if provided
    if phone and (len(phone) < 10 or len(phone) > 20):
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="invalid_phone"))
    
    # Check for duplicate registrations by name (exact match not allowed)
    is_duplicate, duplicate_fields, existing_rsvp = check_rsvp_duplicate(event_uuid, name, email, phone)
    
    if is_duplicate:
        # Exact name match - don't allow
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="name_taken"))
    
    if duplicate_fields:
        # Email or phone already used - store in session and redirect with warning flag
        session[f'pending_rsvp_{event_uuid}'] = {
            'name': name,
            'email': email,
            'phone': phone,
            'additional_info': additional_info
        }
        
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_warning=1, show_confirm=1))
    
    # Save RSVP with verification token and segment handling
    verification_token = str(uuid.uuid4())
    segment_success = None
    segment_error = None
    
    # Add to segment if segment_id is provided
    if segment_id:
        segment_success, segment_error = add_contact_to_segment(email, name, segment_id)
    
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO rsvps (event_uuid, name, email, phone, additional_info, verification_token, segment_id, segment_error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (event_uuid, name, email, phone, additional_info, verification_token, segment_id, segment_error))
            conn.commit()
            cur.close()
            conn.close()
            
            # Send the email via Resend
            email_sent = send_verification_email(email, name, verification_token)
            
            if not email_sent:
                print(f"Failed to send verification email to {email}")
                # Still continue with RSVP but note the email failure
            
            # Update registered count
            event, _, _ = get_event_from_db(event_uuid)
            event['registered'] = event.get('registered', 0) + 1
            save_event_to_db(event, None)
            
            return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_success=1))
        except Exception as e:
            return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="save_failed"))
    return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="db_error"))

# --- New Route: Email Verification ---
@app.route("/verify-email/<token>")
def verify_email(token):
    conn = get_db_connection()
    if not conn: return "Database error", 500
    try:
        cur = conn.cursor()
        cur.execute("UPDATE rsvps SET email_verified = TRUE WHERE verification_token = %s RETURNING event_uuid", (token,))
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        if result:
            event_uuid = result[0]
            return render_template("email_verified.html", event_uuid=event_uuid)
        return "Invalid or expired token", 404
    except Exception as e:
        return f"Error: {e}", 500

# --- New Route: Event Admin Page ---
@app.route("/event/<event_uuid>/admin")
def event_admin(event_uuid):
    # Check if admin is authenticated
    if not session.get('admin_authenticated'):
        return redirect(url_for('admin_login', event_uuid=event_uuid))
    
    event, _, _ = get_event_from_db(event_uuid)
    if not event: return "Event not found", 404
    
    conn = get_db_connection()
    rsvps = []
    email_failures = 0
    segment_errors = 0
    if conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name, email, phone, additional_info, email_verified, rsvp_date, segment_error 
            FROM rsvps WHERE event_uuid = %s ORDER BY rsvp_date DESC
        """, (event_uuid,))
        columns = [desc[0] for desc in cur.description]
        rsvps = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        # Check for email verification failures (unverified emails older than 1 hour)
        cur.execute("""
            SELECT COUNT(*) FROM rsvps 
            WHERE event_uuid = %s AND email_verified = FALSE 
            AND rsvp_date < NOW() - INTERVAL '1 hour'
        """, (event_uuid,))
        email_failures = cur.fetchone()[0]
        
        # Check for segment errors
        cur.execute("""
            SELECT COUNT(*) FROM rsvps 
            WHERE event_uuid = %s AND segment_error IS NOT NULL
        """, (event_uuid,))
        segment_errors = cur.fetchone()[0]
        
        cur.close()
        conn.close()
    
    return render_template("event_admin.html", event=event, rsvps=rsvps, email_failures=email_failures, segment_errors=segment_errors)

@app.route("/event/<event_uuid>/admin/login", methods=["GET", "POST"])
def admin_login(event_uuid):
    """Admin login page"""
    event, _, _ = get_event_from_db(event_uuid)
    if not event:
        return "Event not found", 404
    
    # Rate limiting for admin login attempts
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
    if is_rate_limited(f"admin_login_{client_ip}", max_requests=5, window_seconds=900):
        return "Too many login attempts. Please try again later.", 429
    
    if request.method == "POST":
        admin_code = request.form.get('admin_code', '').strip()
        if admin_code and ADMIN_CODE and admin_code == ADMIN_CODE:
            session['admin_authenticated'] = True
            return redirect(url_for('event_admin', event_uuid=event_uuid))
        else:
            return render_template("admin_login.html", event_uuid=event_uuid, event_title=event.get('title', 'Event'), error="Invalid admin code")
    
    return render_template("admin_login.html", event_uuid=event_uuid, event_title=event.get('title', 'Event'))

@app.route("/event/<event_uuid>/rsvp/confirm", methods=["POST"])
def confirm_rsvp_anyway(event_uuid):
    """Confirm RSVP despite duplicate email/phone warning"""
    session_key = f'event_{event_uuid}_authenticated'
    if not session.get(session_key):
        return redirect(url_for('event_page', event_uuid=event_uuid))
    
    event, _, _ = get_event_from_db(event_uuid)
    if not event:
        return "Event not found", 404
    
    # Get RSVP data from session
    pending_rsvp = session.get(f'pending_rsvp_{event_uuid}')
    if not pending_rsvp:
        return redirect(url_for('event_page', event_uuid=event_uuid))
    
    name = pending_rsvp.get('name', '').strip()
    email = pending_rsvp.get('email', '').strip()
    phone = pending_rsvp.get('phone', '').strip()
    additional_info = pending_rsvp.get('additional_info', '').strip()
    
    # Validate required fields
    if not name or not email:
        return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="Name and email are required"))
    
    # Save RSVP to database
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO rsvps (event_uuid, name, email, phone, additional_info)
                VALUES (%s, %s, %s, %s, %s)
            """, (event_uuid, name, email, phone, additional_info))
            conn.commit()
            cur.close()
            conn.close()
            
            # Update registered count in event data
            event['registered'] = event.get('registered', 0) + 1
            save_event_to_db(event, None)
            
            # Clear pending RSVP from session
            session.pop(f'pending_rsvp_{event_uuid}', None)
        except Exception as e:
            print(f"Error saving RSVP to database: {e}")
            return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_error="Error saving RSVP. Please try again."))
    else:
        # Still increment registered count for display
        event['registered'] = event.get('registered', 0) + 1
    
    return redirect(url_for('event_page', event_uuid=event_uuid, rsvp_success=1))

@app.route("/admin/create-event", methods=["GET", "POST"])
def create_event():
    """Admin endpoint to create new events"""
    if request.method == "GET":
        return render_template("create_event.html")
    
    # Verify admin code
    admin_code = request.form.get('admin_code', '').strip()
    if not admin_code or not ADMIN_CODE or admin_code != ADMIN_CODE:
        return render_template("create_event.html", error="Invalid admin code")
    
    # Validate all required fields
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    date = request.form.get('date', '').strip()
    time = request.form.get('time', '').strip()
    location = request.form.get('location', '').strip()
    address = request.form.get('address', '').strip()
    organizer = request.form.get('organizer', '').strip()
    
    # Optional fields
    capacity = request.form.get('capacity', '').strip()
    price = request.form.get('price', '').strip()
    passcode = request.form.get('passcode', '').strip()
    segment_id = request.form.get('segment_id', '').strip()
    image = request.form.get('image', '').strip()
    tags = request.form.get('tags', '').strip()
    
    # Check required fields are present
    if not all([title, description, date, time, location, address, organizer]):
        return render_template("create_event.html", error="All marked fields (*) are required")
    
    # Validate optional numeric fields if provided
    try:
        if capacity:
            capacity = int(capacity)
            if capacity < 1:
                raise ValueError("Capacity must be at least 1")
        else:
            capacity = None
        
        if price:
            price = float(price)
            if price < 0:
                raise ValueError("Price cannot be negative")
        else:
            price = None
    except ValueError as e:
        return render_template("create_event.html", error=f"Invalid capacity or price: {str(e)}")
    
    # Validate image URL if provided
    if image and not isValidUrl(image):
        return render_template("create_event.html", error="Image URL must be a valid URL")
    
    # Create new event data
    event_data = {
        'uuid': str(uuid.uuid4()),
        'title': title,
        'description': description,
        'date': date,
        'time': time,
        'location': location,
        'address': address,
        'capacity': capacity,
        'registered': 0,
        'price': price,
        'organizer': organizer,
        'tags': [tag.strip() for tag in tags.split(',') if tag.strip()] if tags else [],
        'image': image if image else None
    }
    
    # Save to database
    event_uuid = save_event_to_db(event_data, passcode if passcode else None, segment_id if segment_id else None)
    if event_uuid:
        return redirect(url_for('event_created_success', event_uuid=event_uuid, passcode=passcode if passcode else ''))
    else:
        return render_template("create_event.html", error="Failed to create event. Please try again.")

@app.route("/event/<event_uuid>/created", methods=["GET"])
def event_created_success(event_uuid):
    """Show success page with event URLs"""
    event, passcode_hash, segment_id = get_event_from_db(event_uuid)
    if not event:
        return "Event not found", 404
    
    passcode = request.args.get('passcode', '')
    has_passcode = bool(passcode_hash)
    
    # Build event URL
    event_url = request.host_url.rstrip('/') + url_for('event_page', event_uuid=event_uuid)
    
    # Build shareable URL with passcode
    shareable_url = event_url
    if has_passcode and passcode:
        shareable_url = f"{event_url}?password={passcode}"
    
    return render_template("event_created.html", event=event, event_url=event_url, shareable_url=shareable_url, has_passcode=has_passcode)

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
