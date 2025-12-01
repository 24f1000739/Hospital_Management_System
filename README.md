## Hospital Management System – Flask, SQLite, Bootstrap

A role‑based Hospital Management System that connects **Admins**, **Doctors**, and **Patients** in a single responsive web app.

- **Backend**: Flask, Flask‑SQLAlchemy, SQLite  
- **Frontend**: Jinja2 templates + Bootstrap 5 + custom CSS (no custom JavaScript logic)  
- **Database**: Single SQLite file (`instance/hospital.db`) created automatically on first run

The system follows the project guidelines (pure Flask/Jinja/SQLite) but still provides a modern dark UI and clear separation of concerns.

---

## High‑Level Features

- **Role‑based authentication & authorization**
  - Single `user` table with `role` (`admin`, `doctor`, `patient`).
  - Session‑based login with a `login_required` decorator and role checks.

- **Admin module**
  - Dashboard with counts of doctors, patients, and appointments.
  - Manage doctors: create, edit, delete, blacklist, and search by name/specialization.
  - Manage patients: edit, delete, blacklist, and search.
  - View all appointments with filters (all / upcoming / past).
  - View complete **patient history with any doctor**. 

- **Doctor module**
  - Personal dashboard showing upcoming appointments and assigned patients.
  - **Availability planner**: mark 7‑day morning/evening slots that patients can book.
  - Appointment actions: mark as completed, cancel (which re‑opens the slot).
  - **Update Patient History** form per appointment:
    - Visit Type, Tests Done, Diagnosis, Prescription, Medicines, Notes.
    - Saving always creates/updates a `TreatmentRecord` linked one‑to‑one to the appointment.
  - **View Patient History** screen:
    - For a selected patient, shows all visits with this doctor.
    - Columns: Visit No., Visit Type, Tests Done, Diagnosis, Prescription, Medicines.

- **Patient module**
  - Self‑registration and login.
  - Browse departments and doctors; see doctor profiles and current 7‑day availability.
  - Book appointment from available slots with reason for visit.
  - Cancel upcoming appointments (which automatically re‑opens availability).
  - **Patient History** view that shows all visits with recorded treatment data.

- **Data integrity & constraints**
  - `DoctorAvailability` enforces unique (doctor, day, slot_label).
  - `Appointment` enforces unique (doctor, appointment_date, slot_label) for active bookings.
  - `TreatmentRecord` is **one‑to‑one** with `Appointment` – at most one record per visit.

---

## Project Structure

```text
.
├── app.py                    # Flask application (models, routes, logic)
├── requirements.txt          # Python dependencies
├── README.md                 # Project documentation (this file)
├── instance/
│   └── hospital.db           # SQLite database (auto‑created)
├── static/
│   └── css/
│       └── styles.css        # Custom dark / neon theme on top of Bootstrap
└── templates/
    ├── base.html             # Global layout, navbar, flash messages
    ├── home.html             # Public landing page
    ├── profile.html          # Edit profile for any logged‑in user
    ├── auth/
    │   ├── login.html
    │   └── register.html
    ├── admin/
    │   ├── dashboard.html
    │   ├── doctors.html
    │   ├── doctor_form.html
    │   ├── patients.html
    │   ├── patient_form.html
    │   └── appointments.html
    ├── doctor/
    │   ├── dashboard.html
    │   ├── availability.html
    │   ├── appointments.html
    │   ├── assigned_patients.html
    │   ├── patient_history.html       # Update Patient History form for a visit
    │   ├── view_patient_history.html  # Read‑only grid of all treatment records
    │   └── edit_treatment.html
    └── patient/
        ├── dashboard.html             # Departments + upcoming + quick history
        ├── departments.html
        ├── department_detail.html
        ├── doctors.html
        ├── doctor_profile.html
        ├── doctor_availability.html
        └── history.html               # Patient‑side treatment history
```

---

## Data Model (SQLAlchemy)

- **`Department`**
  - `id`, `name`, `description`
  - Drives doctor grouping and patient browsing.

- **`User`**
  - `id`, `role`, `full_name`, `email`, `phone`, `password_hash`,
    `specialization`, `experience_years`, `bio`, `department_id`,
    `is_active`, `is_blacklisted`
  - Relationships:
    - `appointments_as_patient` – all `Appointment` rows where user is patient.
    - `appointments_as_doctor` – all `Appointment` rows where user is doctor.

- **`DoctorAvailability`**
  - `id`, `doctor_id`, `day`, `slot_label`, `is_open`
  - Unique per `(doctor_id, day, slot_label)`.
  - Used by doctor to publish a 7‑day availability grid and by patients for booking.

- **`Appointment`**
  - `id`, `patient_id`, `doctor_id`, `appointment_date`, `slot_label`,
    `status` (`Booked`, `Completed`, `Cancelled`), `reason`, `created_at`
  - Unique per `(doctor_id, appointment_date, slot_label)` for active bookings.
  - When a booking is confirmed, the corresponding `DoctorAvailability.is_open` is set to `False`.

- **`TreatmentRecord`** (one‑to‑one with `Appointment`)
  - `id`, `appointment_id`, `visit_type`, `test_done`, `diagnosis`,
    `prescription`, `medicines`, `notes`, `updated_at`
  - Relationship in `app.py`:
    - On `TreatmentRecord`:  
      `appointment = db.relationship("Appointment", backref=db.backref("treatment_record", uselist=False, lazy=True))`
    - So `appointment.treatment_record` is a **single object**, not a list.
  - Created/updated whenever a doctor saves the Update Patient History form.

---

## Request Flow by Role

### Admin

- Logs in via `/login` (default admin is created automatically):
  - Email: `admin@hms.local`
  - Password: `admin123`
- `/dashboard` shows:
  - Overview cards (doctors, patients, appointments).
  - Searchable doctor/patient lists.
  - Upcoming appointments table with **Patient History** links that open  
    `/admin/view-history/<patient_id>/<doctor_id>`.

### Doctor

- `/dashboard` (doctor role)
  - Upcoming appointments with buttons to **Update** (open history form),
    **Mark Complete**, and **Cancel`.
  - Assigned Patients section that links to `/doctor/view-history/<patient_id>`.

- `/doctor/availability`
  - 7‑day grid, each row with Morning and Evening checkboxes.
  - Posting the form clears existing slots for that window and recreates them.

- `/doctor/patients/<appointment_id>` – **Update Patient History**
  - Shows visit details (patient, department, date, time).
  - Doctor fills Visit Type, Tests Done, Diagnosis, Prescription, Medicines, Notes.
  - On **Save**:
    - Creates/updates the `TreatmentRecord` for this appointment.
    - Redirects to `/doctor/view-history/<patient_id>`,
      where the doctor sees all saved treatment records for that patient.

- `/doctor/view-history/<patient_id>` – **View Patient History**
  - Query joins `Appointment` + `TreatmentRecord` only for the logged‑in doctor.
  - Table columns: Visit No., Visit Type, Tests Done, Diagnosis, Prescription, Medicines.
  - Text columns preserve line breaks for readability.

### Patient

- `/dashboard` (patient role)
  - Shows departments, upcoming appointments, and a small history section.

- Browsing & booking
  - `/patient/departments` → `/patient/departments/<id>` → `/patient/doctors` → doctor profile.
  - `/patient/doctors/<doctor_id>/availability` lists the doctor’s open slots.
  - `/patient/book/<doctor_id>` posts the selected availability id to create an `Appointment`.

- `/patient/history`
  - Joins `Appointment` + `TreatmentRecord` for the logged‑in patient across all doctors.
  - Displays Visit Type, Doctor, Department, Tests Done, Diagnosis, Prescription, Medicines.

---

## Running the Application

1. **Create and activate a virtual environment** (recommended)
   - Windows (PowerShell):
     ```powershell
     python -m venv .venv
     .venv\Scripts\Activate.ps1
     ```
   - macOS / Linux (bash/zsh):
     ```bash
     python -m venv .venv
     source .venv/bin/activate
     ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the development server**
   ```bash
   flask --app app run
   ```
   or
   ```bash
   python app.py
   ```

4. Open `http://127.0.0.1:5000` in your browser.

On first run the app will:
- Create all database tables.
- Seed standard departments (Cardiology, Neurology, ENT, etc.).
- Create the default admin user.

---


This README is designed so that you can directly use it in your project submission to explain the **architecture**, **database design**, and **flow** of the Hospital Management System.




