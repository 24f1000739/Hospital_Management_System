import os
from datetime import datetime, date, timedelta
from functools import wraps
from typing import Optional

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    g,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, or_, inspect, text
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash, check_password_hash


BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, instance_relative_config=True)
os.makedirs(app.instance_path, exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(app.instance_path, "hospital.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "super-secret-key-change-me"

db = SQLAlchemy(app)

# Milestone: Database Models and Schema Setup
class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

    doctors = db.relationship("User", backref="department", lazy=True)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50))
    password_hash = db.Column(db.String(255), nullable=False)
    specialization = db.Column(db.String(120))
    experience_years = db.Column(db.Integer)
    bio = db.Column(db.Text)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"))
    is_active = db.Column(db.Boolean, default=True)
    is_blacklisted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    appointments_as_patient = db.relationship(
        "Appointment", foreign_keys="Appointment.patient_id", backref="patient", lazy=True
    )
    appointments_as_doctor = db.relationship(
        "Appointment", foreign_keys="Appointment.doctor_id", backref="doctor", lazy=True
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password.strip())

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password.strip())


class DoctorAvailability(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    day = db.Column(db.Date, nullable=False)
    slot_label = db.Column(db.String(50), nullable=False)
    is_open = db.Column(db.Boolean, default=True)

    doctor = db.relationship("User", backref="availabilities", lazy=True)

    __table_args__ = (
        UniqueConstraint("doctor_id", "day", "slot_label", name="unique_slot_per_doctor"),
    )


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    slot_label = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default="Booked")
    reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_by = db.Column(db.String(20))


class TreatmentRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id"), unique=True, nullable=False)
    visit_type = db.Column(db.String(50))
    test_done = db.Column(db.Text)
    diagnosis = db.Column(db.Text)
    prescription = db.Column(db.Text)
    medicines = db.Column(db.Text)
    notes = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    appointment = db.relationship(
        "Appointment",
        backref=db.backref("treatment_record", uselist=False, lazy=True),
        lazy=True,
    )


class AppointmentTimeline(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id"), nullable=False)
    actor_role = db.Column(db.String(20))
    actor_name = db.Column(db.String(120))
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    appointment = db.relationship(
        "Appointment",
        backref=db.backref("timeline_events", lazy="dynamic", cascade="all, delete-orphan"),
        lazy=True,
    )


def reopen_availability_slot(appointment: Appointment) -> None:
    """Mark related availability slot as open so it can be booked again."""
    availability = DoctorAvailability.query.filter_by(
        doctor_id=appointment.doctor_id,
        day=appointment.appointment_date,
        slot_label=appointment.slot_label,
    ).first()
    if availability:
        availability.is_open = True


def record_timeline_event(
    appointment: Appointment,
    message: str,
    actor_role: Optional[str] = None,
    actor_name: Optional[str] = None,
) -> None:
    """Persist a short message describing what happened to an appointment."""
    if not appointment:
        return
    event = AppointmentTimeline(
        appointment=appointment,
        actor_role=actor_role,
        actor_name=actor_name,
        message=message.strip(),
    )
    db.session.add(event)


def drop_unique_indexes(table_name: str) -> None:
    """Drop custom unique indexes so slots can be reused when status changes."""
    try:
        with db.engine.connect() as conn:
            index_rows = conn.execute(text(f"PRAGMA index_list('{table_name}')")).fetchall()
            for row in index_rows:
                # SQLite returns tuples unless row factory changes: (seq, name, unique, origin, partial)
                if hasattr(row, "_mapping"):
                    mapping = row._mapping
                    name = mapping.get("name")
                    is_unique = mapping.get("unique")
                elif isinstance(row, tuple):
                    name = row[1]
                    is_unique = row[2]
                else:
                    name = None
                    is_unique = None
                if not name or str(name).startswith("sqlite_autoindex"):
                    continue
                if is_unique:
                    conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
            conn.commit()
    except Exception as exc:
        print(f"Warning: Could not drop indexes for {table_name}: {exc}")


def to_int(value):
    if value in (None, "", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def migrate_database() -> None:
    """Add missing columns to existing database tables if they don't exist."""
    try:
        inspector = inspect(db.engine)
        
        # Get list of all tables
        tables = inspector.get_table_names()
        
        # Check and add is_blacklisted column to user table
        if 'user' in tables:
            try:
                user_columns = [col['name'] for col in inspector.get_columns('user')]
                if 'is_blacklisted' not in user_columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE user ADD COLUMN is_blacklisted BOOLEAN DEFAULT 0"))
                        conn.commit()
                        print("✓ Added is_blacklisted column to user table")
            except Exception as e:
                print(f"Warning: Could not add is_blacklisted column: {e}")
        
        # Check and add new columns to treatment_record table
        if 'treatment_record' in tables:
            try:
                treatment_columns = [col['name'] for col in inspector.get_columns('treatment_record')]
                
                if 'visit_type' not in treatment_columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE treatment_record ADD COLUMN visit_type VARCHAR(50)"))
                        conn.commit()
                        print("✓ Added visit_type column to treatment_record table")
                
                if 'test_done' not in treatment_columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE treatment_record ADD COLUMN test_done TEXT"))
                        conn.commit()
                        print("✓ Added test_done column to treatment_record table")
                
                if 'medicines' not in treatment_columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE treatment_record ADD COLUMN medicines TEXT"))
                        conn.commit()
                        print("✓ Added medicines column to treatment_record table")
            except Exception as e:
                print(f"Warning: Could not add columns to treatment_record: {e}")

        # Update appointment table to support slot reuse tracking
        if 'appointment' in tables:
            try:
                appointment_columns = [col['name'] for col in inspector.get_columns('appointment')]
                
                if 'updated_at' not in appointment_columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE appointment ADD COLUMN updated_at DATETIME"))
                        conn.commit()
                        print("✓ Added updated_at column to appointment table")
                
                if 'cancelled_by' not in appointment_columns:
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE appointment ADD COLUMN cancelled_by VARCHAR(20)"))
                        conn.commit()
                        print("✓ Added cancelled_by column to appointment table")
                
                # Drop legacy unique indexes so completed/cancelled slots can be re-booked
                drop_unique_indexes("appointment")
            except Exception as e:
                print(f"Warning: Could not update appointment table: {e}")
    except Exception as e:
        # This is normal for new databases
        pass


def bootstrap_data() -> None:
    db.create_all()
    
    # Run migrations to add missing columns
    try:
        migrate_database()
    except Exception as e:
        print(f"Migration warning: {e}")

    all_departments = [
        Department(name="Cardiology", description="Heart and blood vessel care."),
        Department(name="Neurology", description="Brain and nervous system."),
        Department(name="Oncology", description="Cancer diagnosis and treatment."),
        Department(name="Pediatrics", description="Child health and wellness."),
        Department(name="Orthopedics", description="Bone, joint, and muscle treatment."),
        Department(name="Dermatology", description="Skin, hair, and nail care."),
        Department(name="Psychiatry", description="Mental health and behavioral disorders."),
        Department(name="Gastroenterology", description="Digestive system and liver care."),
        Department(name="Ophthalmology", description="Eye and vision care."),
        Department(name="ENT", description="Ear, nose, and throat specialists."),
        Department(name="Urology", description="Urinary tract and male reproductive health."),
        Department(name="Gynecology", description="Women's reproductive health care."),
    ]
    
    existing_names = {dept.name for dept in Department.query.all()}
    new_departments = [dept for dept in all_departments if dept.name not in existing_names]
    
    if new_departments:
        db.session.add_all(new_departments)
        db.session.commit()

    admin_email = "admin@hms.local"
    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(
            role="admin",
            full_name="System Admin",
            email=admin_email,
            phone="0000000000",
        )
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()


with app.app_context():
    bootstrap_data()


def get_current_user():
    user_id = session.get("user_id")
    if user_id:
        return User.query.get(user_id)
    return None

# Milestone: Authentication and Role-Based Access
def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not g.user:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if role and g.user.role != role:
                flash("You are not authorized to view this page.", "danger")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@app.before_request
def load_user():
    g.user = get_current_user()


@app.route("/")
def index():
    departments = Department.query.all()
    stats = {
        "doctors": User.query.filter_by(role="doctor").count(),
        "patients": User.query.filter_by(role="patient").count(),
        "appointments": Appointment.query.count(),
    }
    return render_template("home.html", departments=departments, stats=stats)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username") or request.form.get("email")
        password = request.form["password"]
        
        if not username:
            flash("Username is required.", "danger")
            return redirect(url_for("register"))
        
        # Use username as email for simplicity (wireframe shows only username)
        email = username if "@" in username else f"{username}@hms.local"
        full_name = username.split("@")[0] if "@" in username else username
        
        if User.query.filter_by(email=email).first():
            flash("Username already registered. Please log in.", "danger")
            return redirect(url_for("login"))

        patient = User(role="patient", full_name=full_name, email=email, phone=None)
        patient.set_password(password)
        db.session.add(patient)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username") or request.form.get("email")
        password = request.form["password"]
        user = User.query.filter(
            or_(User.email == username, User.full_name.ilike(username))
        ).first()
        if user and not user.is_active:
            flash("Account is inactive. Contact the administrator.", "danger")
        elif user and user.check_password(password):
            session["user_id"] = user.id
            flash(f"Welcome back, {user.full_name.split()[0]}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("auth/login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

# Milestone: Admin Dashboard and Management
@app.route("/dashboard")
@login_required()
def dashboard():
    if g.user.role == "admin":
        search_query = request.args.get("q", "")
        doctors_query = User.query.filter_by(role="doctor")
        patients_query = User.query.filter_by(role="patient")
        
        if search_query:
            from sqlalchemy import func
            doctors_query = doctors_query.filter(
                or_(
                    User.full_name.ilike(f"%{search_query}%"),
                    User.email.ilike(f"%{search_query}%"),
                    User.specialization.ilike(f"%{search_query}%")
                )
            )
            patients_query = patients_query.filter(
                or_(
                    User.full_name.ilike(f"%{search_query}%"),
                    User.email.ilike(f"%{search_query}%"),
                    User.phone.ilike(f"%{search_query}%")
                )
            )
        
        doctors = doctors_query.all()
        patients = patients_query.all()
        upcoming = (
            Appointment.query.filter(Appointment.appointment_date >= date.today())
            .order_by(Appointment.appointment_date.desc(), Appointment.created_at.desc())
            .limit(15)
            .all()
        )
        counts = {
            "doctors": User.query.filter_by(role="doctor").count(),
            "patients": User.query.filter_by(role="patient").count(),
            "appointments": Appointment.query.count(),
        }
        return render_template(
            "admin/dashboard.html",
            doctors=doctors,
            patients=patients,
            upcoming=upcoming,
            counts=counts,
            search_query=search_query,
        )

    if g.user.role == "doctor":
        today = date.today()
        # Show upcoming appointments (today and future) with status Booked
        upcoming = (
            Appointment.query.filter_by(doctor_id=g.user.id)
            .filter(Appointment.appointment_date >= today)
            .filter(Appointment.status == "Booked")
            .order_by(Appointment.appointment_date)
            .all()
        )
        recent_activity = (
            Appointment.query.filter_by(doctor_id=g.user.id)
            .filter(Appointment.status.in_(["Cancelled", "Completed"]))
            .order_by(Appointment.updated_at.desc(), Appointment.created_at.desc())
            .limit(5)
            .all()
        )
        timeline_events = (
            AppointmentTimeline.query.join(Appointment)
            .filter(Appointment.doctor_id == g.user.id)
            .order_by(AppointmentTimeline.created_at.desc())
            .limit(10)
            .all()
        )
        patients = {appt.patient for appt in g.user.appointments_as_doctor}
        return render_template(
            "doctor/dashboard.html",
            upcoming=upcoming,
            recent_activity=recent_activity,
            patients=patients,
            timeline_events=timeline_events,
        )

    # Patient dashboard
    departments = Department.query.all()
    doctors = User.query.filter_by(role="doctor", is_active=True).all()
    upcoming = (
        Appointment.query.filter_by(patient_id=g.user.id)
        .filter(Appointment.appointment_date >= date.today())
        .filter(Appointment.status == "Booked")
        .order_by(Appointment.appointment_date)
        .all()
    )
    history = (
        Appointment.query.filter_by(patient_id=g.user.id)
        .filter(Appointment.appointment_date < date.today())
        .order_by(Appointment.appointment_date.desc())
        .all()
    )
    return render_template(
        "patient/dashboard.html",
        departments=departments,
        doctors=doctors,
        upcoming=upcoming,
        history=history,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required()
def edit_profile():
    if request.method == "POST":
        g.user.full_name = request.form["full_name"]
        g.user.phone = request.form.get("phone")
        if g.user.role == "doctor":
            g.user.specialization = request.form.get("specialization")
            g.user.experience_years = to_int(request.form.get("experience_years"))
            g.user.bio = request.form.get("bio")
            g.user.department_id = to_int(request.form.get("department_id"))
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("edit_profile"))
    departments = Department.query.all()
    return render_template("profile.html", departments=departments)


@app.route("/admin/doctors", methods=["GET", "POST"])
@login_required(role="admin")
def manage_doctors():
    departments = Department.query.all()
    if request.method == "POST":
        if User.query.filter_by(email=request.form["email"]).first():
            flash("Email already exists. Choose a different email.", "danger")
            return redirect(url_for("manage_doctors"))
        doctor = User(
            role="doctor",
            full_name=request.form["full_name"],
            email=request.form["email"],
            phone=request.form.get("phone"),
            specialization=request.form.get("specialization"),
            experience_years=to_int(request.form.get("experience_years")),
            department_id=to_int(request.form.get("department_id")),
        )
        temp_password = request.form.get("password") or "doctor123"
        doctor.set_password(temp_password)
        db.session.add(doctor)
        db.session.commit()
        flash("Doctor profile created.", "success")
        return redirect(url_for("manage_doctors"))

    search = request.args.get("q")
    query = User.query.filter_by(role="doctor")
    if search:
        query = query.filter(
            or_(
                User.full_name.ilike(f"%{search}%"),
                User.specialization.ilike(f"%{search}%"),
            )
        )
    doctors = query.order_by(User.full_name).all()
    return render_template("admin/doctors.html", doctors=doctors, departments=departments)


@app.route("/admin/doctors/<int:doctor_id>/edit", methods=["GET", "POST"])
@login_required(role="admin")
def edit_doctor(doctor_id):
    doctor = User.query.filter_by(id=doctor_id, role="doctor").first_or_404()
    departments = Department.query.all()
    if request.method == "POST":
        doctor.full_name = request.form["full_name"]
        doctor.phone = request.form.get("phone")
        doctor.specialization = request.form.get("specialization")
        doctor.experience_years = to_int(request.form.get("experience_years"))
        doctor.department_id = to_int(request.form.get("department_id"))
        doctor.bio = request.form.get("bio")
        doctor.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Doctor updated.", "success")
        return redirect(url_for("manage_doctors"))
    return render_template("admin/doctor_form.html", doctor=doctor, departments=departments)


@app.route("/admin/doctors/<int:doctor_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_doctor(doctor_id):
    doctor = User.query.filter_by(id=doctor_id, role="doctor").first_or_404()
    
    # Delete all related appointments and treatment records
    appointments = Appointment.query.filter_by(doctor_id=doctor_id).all()
    for appt in appointments:
        if appt.treatment_record:
            db.session.delete(appt.treatment_record)
        db.session.delete(appt)
    
    # Delete all doctor availability slots
    DoctorAvailability.query.filter_by(doctor_id=doctor_id).delete()
    
    # Delete the doctor
    db.session.delete(doctor)
    db.session.commit()
    flash("Doctor and all related data deleted.", "success")
    return redirect(url_for("manage_doctors"))


@app.route("/admin/patients")
@login_required(role="admin")
def manage_patients():
    search = request.args.get("q")
    query = User.query.filter_by(role="patient")
    if search:
        query = query.filter(
            or_(
                User.full_name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.phone.ilike(f"%{search}%"),
            )
        )
    patients = query.order_by(User.full_name).all()
    return render_template("admin/patients.html", patients=patients)


@app.route("/admin/patients/<int:patient_id>/edit", methods=["GET", "POST"])
@login_required(role="admin")
def edit_patient(patient_id):
    patient = User.query.filter_by(id=patient_id, role="patient").first_or_404()
    if request.method == "POST":
        patient.full_name = request.form["full_name"]
        patient.phone = request.form.get("phone")
        patient.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Patient profile updated.", "success")
        return redirect(url_for("manage_patients"))
    return render_template("admin/patient_form.html", patient=patient)


@app.route("/admin/patients/<int:patient_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_patient(patient_id):
    patient = User.query.filter_by(id=patient_id, role="patient").first_or_404()
    
    # Delete all related appointments and treatment records
    appointments = Appointment.query.filter_by(patient_id=patient_id).all()
    for appt in appointments:
        if appt.treatment_record:
            db.session.delete(appt.treatment_record)
        db.session.delete(appt)
    
    # Delete the patient
    db.session.delete(patient)
    db.session.commit()
    flash("Patient and all related data deleted.", "success")
    return redirect(url_for("manage_patients"))


@app.route("/admin/doctors/<int:doctor_id>/blacklist", methods=["POST"])
@login_required(role="admin")
def blacklist_doctor(doctor_id):
    doctor = User.query.filter_by(id=doctor_id, role="doctor").first_or_404()
    doctor.is_blacklisted = not doctor.is_blacklisted
    db.session.commit()
    action = "blacklisted" if doctor.is_blacklisted else "removed from blacklist"
    flash(f"Doctor {action}.", "info")
    return redirect(url_for("manage_doctors"))


@app.route("/admin/patients/<int:patient_id>/blacklist", methods=["POST"])
@login_required(role="admin")
def blacklist_patient(patient_id):
    patient = User.query.filter_by(id=patient_id, role="patient").first_or_404()
    patient.is_blacklisted = not patient.is_blacklisted
    db.session.commit()
    action = "blacklisted" if patient.is_blacklisted else "removed from blacklist"
    flash(f"Patient {action}.", "info")
    return redirect(url_for("manage_patients"))


@app.route("/admin/appointments")
@login_required(role="admin")
def admin_appointments():
    filter_type = request.args.get("filter", "all")  # all, upcoming, past
    today = date.today()
    
    query = Appointment.query
    if filter_type == "upcoming":
        query = query.filter(Appointment.appointment_date >= today)
    elif filter_type == "past":
        query = query.filter(Appointment.appointment_date < today)
    
    appointments = query.order_by(Appointment.appointment_date.desc(), Appointment.created_at.desc()).all()
    return render_template("admin/appointments.html", appointments=appointments, filter_type=filter_type)


@app.route("/doctor/mark-complete", methods=["POST"])
@login_required(role="doctor")
def mark_complete_appointment():
    appointment = Appointment.query.filter_by(id=request.form["appointment_id"], doctor_id=g.user.id).first_or_404()
    if appointment.status == "Booked":
        # If no treatment record exists, create one with default values
        if not appointment.treatment_record:
            record = TreatmentRecord(
                appointment_id=appointment.id,
                visit_type="In-person",
            )
            db.session.add(record)
        appointment.status = "Completed"
        appointment.cancelled_by = None
        appointment.updated_at = datetime.utcnow()
        reopen_availability_slot(appointment)
        record_timeline_event(
            appointment,
            f"Dr. {g.user.full_name} marked the visit complete for {appointment.patient.full_name}.",
            actor_role="doctor",
            actor_name=g.user.full_name,
        )
        db.session.commit()
        flash("Appointment marked as completed.", "success")
    else:
        flash("Appointment is already completed or cancelled.", "warning")
    return redirect(url_for("dashboard"))


@app.route("/doctor/cancel-appointment", methods=["POST"])
@login_required(role="doctor")
def cancel_appointment():
    appointment = Appointment.query.filter_by(id=request.form["appointment_id"], doctor_id=g.user.id).first_or_404()
    appointment.status = "Cancelled"
    appointment.cancelled_by = "doctor"
    appointment.updated_at = datetime.utcnow()
    reopen_availability_slot(appointment)
    record_timeline_event(
        appointment,
        f"Dr. {g.user.full_name} cancelled {appointment.slot_label} on {appointment.appointment_date.strftime('%d %b %Y')}.",
        actor_role="doctor",
        actor_name=g.user.full_name,
    )
    db.session.commit()
    flash("Appointment cancelled.", "info")
    return redirect(url_for("dashboard"))


@app.route("/doctor/appointments", methods=["GET", "POST"])
@login_required(role="doctor")
def doctor_appointments():
    appointments = (
        Appointment.query.filter_by(doctor_id=g.user.id)
        .filter(Appointment.appointment_date >= date.today())
        .order_by(Appointment.appointment_date.asc())
        .all()
    )
    return render_template("doctor/appointments.html", appointments=appointments)


@app.route("/doctor/availability", methods=["GET", "POST"])
@login_required(role="doctor")
def doctor_availability():
    window_start = date.today()
    window_end = date.today() + timedelta(days=7)
    
    if request.method == "POST":
        # Clear existing availability for next 7 days
        DoctorAvailability.query.filter_by(doctor_id=g.user.id).filter(
            DoctorAvailability.day >= window_start,
            DoctorAvailability.day <= window_end
        ).delete()
        
        # Process form data - checkboxes for each day
        current_date = window_start
        morning_slot = "08:00 - 12:00 am"
        evening_slot = "04:00 - 9:00 pm"
        
        while current_date <= window_end:
            date_str = current_date.strftime('%Y-%m-%d')
            
            # Check if morning slot is selected
            if request.form.get(f"morning_{date_str}"):
                morning_avail = DoctorAvailability(
                    doctor_id=g.user.id,
                    day=current_date,
                    slot_label=morning_slot,
                    is_open=True
                )
                db.session.add(morning_avail)
            
            # Check if evening slot is selected
            if request.form.get(f"evening_{date_str}"):
                evening_avail = DoctorAvailability(
                    doctor_id=g.user.id,
                    day=current_date,
                    slot_label=evening_slot,
                    is_open=True
                )
                db.session.add(evening_avail)
            
            current_date += timedelta(days=1)
        
        try:
            db.session.commit()
            flash("Availability updated for next 7 days.", "success")
        except Exception as e:
            db.session.rollback()
            flash("Error updating availability.", "danger")
        return redirect(url_for("dashboard"))

    # Get next 7 days availability
    window_start = date.today()
    window_end = date.today() + timedelta(days=7)
    upcoming = (
        DoctorAvailability.query.filter_by(doctor_id=g.user.id)
        .filter(DoctorAvailability.day >= window_start, DoctorAvailability.day <= window_end)
        .order_by(DoctorAvailability.day.asc())
        .all()
    )
    
    # Group by date
    availability_by_date = {}
    for avail in upcoming:
        if avail.day not in availability_by_date:
            availability_by_date[avail.day] = []
        availability_by_date[avail.day].append(avail)
    
    # Generate list of dates for the next 7 days
    dates_list = []
    current_date = window_start
    for i in range(7):
        dates_list.append(current_date + timedelta(days=i))
    
    return render_template("doctor/availability.html", availability_by_date=availability_by_date, dates_list=dates_list)


@app.route("/doctor/patients")
@login_required(role="doctor")
def doctor_assigned_patients():
    patients = {appt.patient for appt in g.user.appointments_as_doctor}
    patient_id = request.args.get("patient_id")
    if patient_id:
        patient = User.query.filter_by(id=patient_id, role="patient").first_or_404()
        appointments = (
            Appointment.query.filter_by(patient_id=patient_id, doctor_id=g.user.id)
            .order_by(Appointment.appointment_date.desc())
            .all()
        )
        if appointments:
            return redirect(url_for("doctor_patient_history", appointment_id=appointments[0].id))
    return render_template("doctor/assigned_patients.html", patients=patients)


@app.route("/doctor/view-history/<int:patient_id>")
@login_required(role="doctor")
def view_patient_history_full(patient_id):
    patient = User.query.filter_by(id=patient_id, role="patient").first_or_404()
    history = (
        Appointment.query.filter_by(patient_id=patient_id, doctor_id=g.user.id)
        .join(TreatmentRecord, Appointment.id == TreatmentRecord.appointment_id)
        .options(joinedload(Appointment.treatment_record))
        .order_by(Appointment.appointment_date.desc())
        .all()
    )
    return render_template("doctor/view_patient_history.html", patient=patient, history=history, doctor=g.user)


@app.route("/doctor/patients/<int:appointment_id>", methods=["GET", "POST"])
@login_required(role="doctor")
def doctor_patient_history(appointment_id):
    appointment = Appointment.query.filter_by(id=appointment_id, doctor_id=g.user.id).first_or_404()
    if request.method == "POST":
        # Always save/update treatment details so they appear in patient history
        record = appointment.treatment_record
        if not record:
            record = TreatmentRecord(appointment_id=appointment.id)
            db.session.add(record)
        
        record.visit_type = request.form.get("visit_type") or None
        record.test_done = request.form.get("test_done") or None
        record.diagnosis = request.form.get("diagnosis") or None
        record.prescription = request.form.get("prescription") or None
        record.medicines = request.form.get("medicines") or None
        record.notes = request.form.get("notes") or None

        # Saving treatment details means appointment is complete
        appointment.status = "Completed"
        appointment.cancelled_by = None
        appointment.updated_at = datetime.utcnow()
        reopen_availability_slot(appointment)
        record_timeline_event(
            appointment,
            f"Treatment details saved and visit marked complete by Dr. {g.user.full_name}.",
            actor_role="doctor",
            actor_name=g.user.full_name,
        )
        
        db.session.commit()
        flash("Treatment record saved. Patient history has been updated.", "success")
        return redirect(url_for("view_patient_history_full", patient_id=appointment.patient_id))

    from sqlalchemy.orm import joinedload
    history = (
        Appointment.query.filter_by(patient_id=appointment.patient_id, doctor_id=g.user.id)
        .join(TreatmentRecord, Appointment.id == TreatmentRecord.appointment_id)
        .options(joinedload(Appointment.treatment_record))
        .order_by(Appointment.appointment_date.desc())
        .all()
    )
    return render_template("doctor/patient_history.html", appointment=appointment, history=history)


@app.route("/admin/view-history/<int:patient_id>/<int:doctor_id>")
@login_required(role="admin")
def admin_view_patient_history(patient_id, doctor_id):
    patient = User.query.filter_by(id=patient_id, role="patient").first_or_404()
    doctor = User.query.filter_by(id=doctor_id, role="doctor").first_or_404()
    history = (
        Appointment.query.filter_by(patient_id=patient_id, doctor_id=doctor_id)
        .join(TreatmentRecord, Appointment.id == TreatmentRecord.appointment_id)
        .options(joinedload(Appointment.treatment_record))
        .order_by(Appointment.appointment_date.desc())
        .all()
    )
    return render_template("admin/patient_history.html", patient=patient, doctor=doctor, history=history)


@app.route("/doctor/edit-treatment/<int:appointment_id>", methods=["GET", "POST"])
@login_required(role="doctor")
def edit_treatment_record(appointment_id):
    """Allow doctors to edit treatment records from patient history view"""
    appointment = Appointment.query.filter_by(id=appointment_id, doctor_id=g.user.id).first_or_404()
    
    if not appointment.treatment_record:
        flash("No treatment record found for this appointment.", "warning")
        return redirect(url_for("view_patient_history_full", patient_id=appointment.patient_id))
    
    if request.method == "POST":
        # Update treatment record
        record = appointment.treatment_record
        record.visit_type = request.form.get("visit_type") or None
        record.test_done = request.form.get("test_done") or None
        record.diagnosis = request.form.get("diagnosis") or None
        record.prescription = request.form.get("prescription") or None
        record.medicines = request.form.get("medicines") or None
        record.notes = request.form.get("notes") or None
        
        db.session.commit()
        flash("Treatment record updated successfully.", "success")
        
        # Redirect back to patient history view
        return redirect(url_for("view_patient_history_full", patient_id=appointment.patient_id))
    
    return render_template("doctor/edit_treatment.html", appointment=appointment)


@app.route("/patient/history")
@login_required(role="patient")
def patient_history_view():
    # Include appointments that have treatment records (completed or updated by doctor)
    # Get all appointments for this patient that have treatment records
    # This ensures that when a doctor saves treatment details, they appear in patient history
    
    # Get all appointments with treatment records - this includes both completed and saved (not completed) records
    history = (
        Appointment.query.filter_by(patient_id=g.user.id)
        .join(TreatmentRecord, Appointment.id == TreatmentRecord.appointment_id)
        .options(joinedload(Appointment.treatment_record))
        .options(joinedload(Appointment.doctor).joinedload(User.department))
        .order_by(Appointment.appointment_date.desc())
        .all()
    )
    
    return render_template("patient/history.html", history=history)


@app.route("/patient/departments")
@login_required(role="patient")
def patient_departments():
    departments = Department.query.all()
    return render_template("patient/departments.html", departments=departments)


@app.route("/patient/departments/<int:dept_id>")
@login_required(role="patient")
def department_detail(dept_id):
    department = Department.query.get_or_404(dept_id)
    doctors = User.query.filter_by(role="doctor", department_id=dept_id, is_active=True).all()
    return render_template("patient/department_detail.html", department=department, doctors=doctors)


@app.route("/patient/doctors")
@login_required(role="patient")
def patient_doctors():
    specialization = request.args.get("specialization")
    dept_id = request.args.get("department_id")
    query = User.query.filter_by(role="doctor", is_active=True)
    if specialization:
        query = query.filter(User.specialization.ilike(f"%{specialization}%"))
    if dept_id:
        query = query.filter(User.department_id == to_int(dept_id))
    doctors = query.all()
    departments = Department.query.all()
    return render_template("patient/doctors.html", doctors=doctors, departments=departments)


@app.route("/patient/doctors/<int:doctor_id>/profile")
@login_required(role="patient")
def doctor_profile(doctor_id):
    doctor = User.query.filter_by(id=doctor_id, role="doctor", is_active=True).first_or_404()
    return render_template("patient/doctor_profile.html", doctor=doctor)


@app.route("/patient/doctors/<int:doctor_id>/availability")
@login_required(role="patient")
def doctor_availability_view(doctor_id):
    doctor = User.query.filter_by(id=doctor_id, role="doctor", is_active=True).first_or_404()
    window_start = date.today()
    window_end = date.today() + timedelta(days=7)
    
    # Get all slots (both open and booked) for the next 7 days
    all_slots = (
        DoctorAvailability.query.filter_by(doctor_id=doctor.id)
        .filter(DoctorAvailability.day >= window_start)
        .filter(DoctorAvailability.day <= window_end)
        .order_by(DoctorAvailability.day.asc())
        .all()
    )
    
    # Group slots by date
    slots_by_date = {}
    for slot in all_slots:
        if slot.day not in slots_by_date:
            slots_by_date[slot.day] = []
        slots_by_date[slot.day].append(slot)
    
    # Generate list of dates for the next 7 days
    dates_list = []
    current_date = window_start
    for i in range(7):
        dates_list.append(current_date + timedelta(days=i))
    
    return render_template("patient/doctor_availability.html", doctor=doctor, slots_by_date=slots_by_date, dates_list=dates_list)


@app.route("/patient/book/<int:doctor_id>", methods=["GET", "POST"])
@login_required(role="patient")
def book_appointment(doctor_id):
    doctor = User.query.filter_by(id=doctor_id, role="doctor", is_active=True).first_or_404()
    
    if request.method == "POST":
        availability_id = request.form.get("availability_id")
        if not availability_id:
            flash("Please select a time slot.", "danger")
            return redirect(url_for("doctor_availability_view", doctor_id=doctor.id))
        
        availability = DoctorAvailability.query.filter_by(id=availability_id, doctor_id=doctor.id).first_or_404()
        
        # Check if slot is still open
        if not availability.is_open:
            flash("Selected slot is no longer available.", "danger")
            return redirect(url_for("doctor_availability_view", doctor_id=doctor.id))
        
        # Check for duplicate appointment (same doctor, date, and slot)
        existing = Appointment.query.filter_by(
            doctor_id=doctor.id,
            appointment_date=availability.day,
            slot_label=availability.slot_label,
            status="Booked"
        ).first()
        
        if existing:
            flash("This time slot is already booked. Please select another slot.", "danger")
            return redirect(url_for("doctor_availability_view", doctor_id=doctor.id))
        
        # Create appointment
        appointment = Appointment(
            patient_id=g.user.id,
            doctor_id=doctor.id,
            appointment_date=availability.day,
            slot_label=availability.slot_label,
            reason=request.form.get("reason"),
            status="Booked"
        )
        
        # Mark availability as booked
        availability.is_open = False
        
        try:
            db.session.add(appointment)
            record_timeline_event(
                appointment,
                f"{g.user.full_name} booked {availability.slot_label} on {availability.day.strftime('%d %b %Y')}.",
                actor_role="patient",
                actor_name=g.user.full_name,
            )
            db.session.commit()
            flash("Appointment booked successfully.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            db.session.rollback()
            flash("Error booking appointment. Please try again.", "danger")
            return redirect(url_for("doctor_availability_view", doctor_id=doctor.id))
    
    # GET request - redirect to availability view
    return redirect(url_for("doctor_availability_view", doctor_id=doctor.id))


@app.route("/patient/appointments", methods=["POST"])
@login_required(role="patient")
def patient_appointment_actions():
    appointment = Appointment.query.filter_by(id=request.form["appointment_id"], patient_id=g.user.id).first_or_404()
    action = request.form["action"]
    if action == "cancel" and appointment.status == "Booked":
        appointment.status = "Cancelled"
        appointment.cancelled_by = "patient"
        appointment.updated_at = datetime.utcnow()
        reopen_availability_slot(appointment)
        record_timeline_event(
            appointment,
            f"{appointment.patient.full_name} cancelled {appointment.slot_label} on {appointment.appointment_date.strftime('%d %b %Y')}.",
            actor_role="patient",
            actor_name=appointment.patient.full_name,
        )
    db.session.commit()
    flash("Appointment updated.", "info")
    return redirect(url_for("dashboard"))


@app.context_processor
def inject_helpers():
    def format_date(value):
        return value.strftime("%d %b %Y")

    return {
        "format_date": format_date,
        "today": date.today,
        "datetime": datetime,
        "timedelta": timedelta,
    }


if __name__ == "__main__":
    app.run(debug=True)

