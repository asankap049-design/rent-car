from flask import Flask, render_template, redirect, url_for, flash, request, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime
from functools import wraps
from sqlalchemy import text
from models import db, User, Car, Booking, MaintenanceRecord
from translations import TRANSLATIONS

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rentcar-secret-2024-xk9'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///rentcar.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to book this car.'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.context_processor
def inject_lang():
    lang = session.get('lang', 'en')
    tr = TRANSLATIONS.get(lang, TRANSLATIONS['en'])
    def t(key):
        return tr.get(key, TRANSLATIONS['en'].get(key, key))
    return dict(t=t, current_lang=lang)


@app.route('/lang/<code>')
def set_lang(code):
    if code in ('en', 'si', 'ta'):
        session['lang'] = code
    return redirect(request.referrer or url_for('customer_cars'))


def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'customer':
            flash('Access denied.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'owner':
            flash('Access denied.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def check_date_overlap(car_id, start, end, exclude_booking_id=None):
    """Returns True if dates overlap with an existing confirmed/pending booking."""
    q = Booking.query.filter(
        Booking.car_id == car_id,
        Booking.status.in_(['pending', 'confirmed']),
        Booking.start_date < end,
        Booking.end_date > start,
    )
    if exclude_booking_id:
        q = q.filter(Booking.id != exclude_booking_id)
    return q.first() is not None


# ─────────────────────────────────────────
# ROOT
# ─────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('customer_dashboard') if current_user.role == 'customer' else url_for('owner_dashboard'))
    return redirect(url_for('customer_cars'))


# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=request.form.get('remember'))
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        role = request.form.get('role', 'customer')
        phone = request.form.get('phone', '').strip()

        if not name or not email or not password:
            flash('All fields are required.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
        elif role not in ('customer', 'owner'):
            flash('Invalid role.', 'danger')
        else:
            user = User(
                name=name,
                email=email,
                password_hash=generate_password_hash(password),
                role=role,
                phone=phone,
            )
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(f'Welcome, {name}!', 'success')
            return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# ─────────────────────────────────────────
# CUSTOMER
# ─────────────────────────────────────────

@app.route('/customer/')
@login_required
@customer_required
def customer_dashboard():
    my_bookings = Booking.query.filter_by(customer_id=current_user.id).order_by(Booking.created_at.desc()).all()
    total_trips = len([b for b in my_bookings if b.status == 'completed'])
    active = [b for b in my_bookings if b.status == 'confirmed']
    pending = [b for b in my_bookings if b.status == 'pending']
    total_spent = sum(b.total_price for b in my_bookings if b.status in ('confirmed', 'completed'))
    recent = my_bookings[:5]
    return render_template('customer/dashboard.html',
                           total_trips=total_trips, active=active,
                           pending=pending, total_spent=total_spent, recent=recent)


@app.route('/customer/cars')
def customer_cars():
    search = request.args.get('q', '').strip()
    location = request.args.get('location', '').strip()
    max_price = request.args.get('max_price', '').strip()
    transmission = request.args.get('transmission', '').strip()

    q = Car.query.filter_by(is_available=True)
    if search:
        q = q.filter((Car.make.ilike(f'%{search}%')) | (Car.model.ilike(f'%{search}%')))
    if location:
        q = q.filter(Car.location.ilike(f'%{location}%'))
    if max_price:
        try:
            q = q.filter(Car.price_per_day <= float(max_price))
        except ValueError:
            pass
    if transmission in ('auto', 'manual'):
        q = q.filter_by(transmission=transmission)

    cars = q.order_by(Car.created_at.desc()).all()
    today = date.today().isoformat()
    return render_template('customer/cars.html', cars=cars, today=today,
                           search=search, location=location, max_price=max_price, transmission=transmission)


@app.route('/customer/book/<int:car_id>', methods=['GET', 'POST'])
@login_required
def customer_book(car_id):
    if current_user.role == 'owner':
        flash('Owners cannot make bookings.', 'warning')
        return redirect(url_for('owner_dashboard'))
    car = Car.query.get_or_404(car_id)
    if not car.is_available:
        flash('This car is not available.', 'danger')
        return redirect(url_for('customer_cars'))

    today = date.today()
    if request.method == 'POST':
        try:
            start = date.fromisoformat(request.form['start_date'])
            end = date.fromisoformat(request.form['end_date'])
        except (KeyError, ValueError):
            flash('Invalid dates.', 'danger')
            return redirect(url_for('customer_book', car_id=car_id))

        notes = request.form.get('notes', '').strip()

        if start < today:
            flash('Start date cannot be in the past.', 'danger')
        elif end <= start:
            flash('End date must be after start date.', 'danger')
        elif check_date_overlap(car_id, start, end):
            flash('Car is already booked for those dates. Please choose different dates.', 'danger')
        else:
            days = (end - start).days
            total = days * car.price_per_day
            booking = Booking(
                car_id=car.id,
                customer_id=current_user.id,
                start_date=start,
                end_date=end,
                total_price=total,
                notes=notes,
            )
            db.session.add(booking)
            db.session.commit()
            flash('Booking request sent! Wait for owner confirmation.', 'success')
            return redirect(url_for('customer_bookings'))

    return render_template('customer/book.html', car=car, today=today.isoformat())


@app.route('/customer/bookings')
@login_required
@customer_required
def customer_bookings():
    status_filter = request.args.get('status', '')
    q = Booking.query.filter_by(customer_id=current_user.id)
    if status_filter:
        q = q.filter_by(status=status_filter)
    bookings = q.order_by(Booking.created_at.desc()).all()
    return render_template('customer/bookings.html', bookings=bookings, status_filter=status_filter)


@app.route('/customer/bookings/cancel/<int:booking_id>', methods=['POST'])
@login_required
@customer_required
def customer_cancel(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.customer_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customer_bookings'))
    if booking.status not in ('pending', 'confirmed'):
        flash('Cannot cancel this booking.', 'danger')
    else:
        booking.status = 'cancelled'
        db.session.commit()
        flash('Booking cancelled.', 'info')
    return redirect(url_for('customer_bookings'))


@app.route('/customer/profile', methods=['GET', 'POST'])
@login_required
@customer_required
def customer_profile():
    if request.method == 'POST':
        current_user.name = request.form.get('name', current_user.name).strip()
        current_user.phone = request.form.get('phone', '').strip()
        current_user.address = request.form.get('address', '').strip()
        new_pw = request.form.get('new_password', '').strip()
        if new_pw:
            if len(new_pw) < 6:
                flash('Password must be at least 6 characters.', 'danger')
                return redirect(url_for('customer_profile'))
            current_user.password_hash = generate_password_hash(new_pw)
        db.session.commit()
        flash('Profile updated.', 'success')
    total_bookings = Booking.query.filter_by(customer_id=current_user.id).count()
    total_spent = db.session.query(db.func.sum(Booking.total_price)).filter(
        Booking.customer_id == current_user.id,
        Booking.status.in_(['confirmed', 'completed'])
    ).scalar() or 0
    return render_template('customer/profile.html', total_bookings=total_bookings, total_spent=total_spent)


# ─────────────────────────────────────────
# OWNER
# ─────────────────────────────────────────

@app.route('/owner/')
@login_required
@owner_required
def owner_dashboard():
    my_cars = Car.query.filter_by(owner_id=current_user.id).all()
    car_ids = [c.id for c in my_cars]
    all_bookings = Booking.query.filter(Booking.car_id.in_(car_ids)).all() if car_ids else []
    pending = [b for b in all_bookings if b.status == 'pending']
    confirmed = [b for b in all_bookings if b.status == 'confirmed']
    total_earnings = sum(b.total_price for b in all_bookings if b.status in ('confirmed', 'completed'))
    available_cars = sum(1 for c in my_cars if c.is_available)
    cars_with_alerts = [c for c in my_cars if c.has_alerts]
    return render_template('owner/dashboard.html',
                           my_cars=my_cars, pending=pending, confirmed=confirmed,
                           total_earnings=total_earnings, available_cars=available_cars,
                           recent_bookings=sorted(all_bookings, key=lambda b: b.created_at, reverse=True)[:5],
                           cars_with_alerts=cars_with_alerts)


@app.route('/owner/cars')
@login_required
@owner_required
def owner_cars():
    cars = Car.query.filter_by(owner_id=current_user.id).order_by(Car.created_at.desc()).all()
    return render_template('owner/cars.html', cars=cars)


@app.route('/owner/cars/add', methods=['GET', 'POST'])
@login_required
@owner_required
def owner_car_add():
    if request.method == 'POST':
        try:
            year = int(request.form.get('year', 0))
            price = float(request.form.get('price_per_day', 0))
            seats = int(request.form.get('seats', 5))
        except ValueError:
            flash('Invalid number fields.', 'danger')
            return redirect(url_for('owner_car_add'))

        car = Car(
            owner_id=current_user.id,
            make=request.form.get('make', '').strip(),
            model=request.form.get('model', '').strip(),
            year=year,
            price_per_day=price,
            location=request.form.get('location', '').strip(),
            description=request.form.get('description', '').strip(),
            image_url=request.form.get('image_url', '').strip() or None,
            seats=seats,
            transmission=request.form.get('transmission', 'auto'),
            fuel_type=request.form.get('fuel_type', 'petrol'),
        )
        db.session.add(car)
        db.session.commit()
        flash('Car added successfully!', 'success')
        return redirect(url_for('owner_cars'))
    return render_template('owner/car_form.html', car=None, action='Add')


@app.route('/owner/cars/edit/<int:car_id>', methods=['GET', 'POST'])
@login_required
@owner_required
def owner_car_edit(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))
    if request.method == 'POST':
        try:
            car.year = int(request.form.get('year', car.year))
            car.price_per_day = float(request.form.get('price_per_day', car.price_per_day))
            car.seats = int(request.form.get('seats', car.seats))
        except ValueError:
            flash('Invalid number fields.', 'danger')
            return redirect(url_for('owner_car_edit', car_id=car_id))

        car.make = request.form.get('make', car.make).strip()
        car.model = request.form.get('model', car.model).strip()
        car.location = request.form.get('location', '').strip()
        car.description = request.form.get('description', '').strip()
        car.image_url = request.form.get('image_url', '').strip() or None
        car.transmission = request.form.get('transmission', car.transmission)
        car.fuel_type = request.form.get('fuel_type', car.fuel_type)
        db.session.commit()
        flash('Car updated.', 'success')
        return redirect(url_for('owner_cars'))
    return render_template('owner/car_form.html', car=car, action='Edit')


@app.route('/owner/cars/delete/<int:car_id>', methods=['POST'])
@login_required
@owner_required
def owner_car_delete(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))
    db.session.delete(car)
    db.session.commit()
    flash('Car deleted.', 'info')
    return redirect(url_for('owner_cars'))


@app.route('/owner/cars/toggle/<int:car_id>', methods=['POST'])
@login_required
@owner_required
def owner_car_toggle(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))
    car.is_available = not car.is_available
    db.session.commit()
    flash(f'Car marked as {"available" if car.is_available else "unavailable"}.', 'info')
    return redirect(url_for('owner_cars'))


@app.route('/owner/bookings')
@login_required
@owner_required
def owner_bookings():
    car_ids = [c.id for c in Car.query.filter_by(owner_id=current_user.id).all()]
    status_filter = request.args.get('status', '')
    if car_ids:
        q = Booking.query.filter(Booking.car_id.in_(car_ids))
        if status_filter:
            q = q.filter_by(status=status_filter)
        bookings = q.order_by(Booking.created_at.desc()).all()
    else:
        bookings = []
    return render_template('owner/bookings.html', bookings=bookings, status_filter=status_filter)


@app.route('/owner/bookings/confirm/<int:booking_id>', methods=['POST'])
@login_required
@owner_required
def owner_confirm(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_bookings'))
    booking.status = 'confirmed'
    db.session.commit()
    flash('Booking confirmed!', 'success')
    return redirect(url_for('owner_bookings'))


@app.route('/owner/bookings/reject/<int:booking_id>', methods=['POST'])
@login_required
@owner_required
def owner_reject(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_bookings'))
    booking.status = 'rejected'
    db.session.commit()
    flash('Booking rejected.', 'info')
    return redirect(url_for('owner_bookings'))


@app.route('/owner/bookings/complete/<int:booking_id>', methods=['POST'])
@login_required
@owner_required
def owner_complete(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_bookings'))
    booking.status = 'completed'
    db.session.commit()
    flash('Booking marked as completed.', 'success')
    return redirect(url_for('owner_bookings'))


@app.route('/owner/earnings')
@login_required
@owner_required
def owner_earnings():
    return redirect(url_for('owner_finance'))


@app.route('/owner/finance')
@login_required
@owner_required
def owner_finance():
    cars = Car.query.filter_by(owner_id=current_user.id).all()
    car_ids = [c.id for c in cars]
    if not car_ids:
        return render_template('owner/finance.html',
                               total_income=0, total_cost=0, net_profit=0,
                               pending_income=0, monthly_data={}, per_car=[],
                               all_months=[], recent_bookings=[])

    # ── Income ──
    paid_bookings = Booking.query.filter(
        Booking.car_id.in_(car_ids),
        Booking.status.in_(['confirmed', 'completed'])
    ).order_by(Booking.created_at.desc()).all()

    pending_bookings = Booking.query.filter(
        Booking.car_id.in_(car_ids),
        Booking.status == 'pending'
    ).all()

    total_income = sum(b.total_price for b in paid_bookings)
    pending_income = sum(b.total_price for b in pending_bookings)

    income_by_month = {}
    for b in paid_bookings:
        key = b.created_at.strftime('%Y-%m')
        income_by_month[key] = income_by_month.get(key, 0) + b.total_price

    # ── Cost ──
    all_records = MaintenanceRecord.query.filter(
        MaintenanceRecord.car_id.in_(car_ids)
    ).all()
    total_cost = sum(r.cost or 0 for r in all_records)

    cost_by_month = {}
    for r in all_records:
        key = r.service_date.strftime('%Y-%m')
        cost_by_month[key] = cost_by_month.get(key, 0) + (r.cost or 0)

    # ── Combined monthly ──
    all_months = sorted(set(list(income_by_month.keys()) + list(cost_by_month.keys())))
    monthly_data = {}
    for m in all_months:
        inc = income_by_month.get(m, 0)
        cost = cost_by_month.get(m, 0)
        monthly_data[m] = {'income': inc, 'cost': cost, 'net': inc - cost}

    # ── Per car ──
    per_car = []
    for c in cars:
        income = sum(b.total_price for b in c.bookings if b.status in ('confirmed', 'completed'))
        cost = sum(r.cost or 0 for r in c.maintenance_records)
        trips = len([b for b in c.bookings if b.status == 'completed'])
        net = income - cost
        margin = round(net / income * 100) if income > 0 else 0
        per_car.append({'car': c, 'income': income, 'cost': cost, 'net': net, 'margin': margin, 'trips': trips})
    per_car.sort(key=lambda x: x['net'], reverse=True)

    net_profit = total_income - total_cost
    profit_margin = round(net_profit / total_income * 100) if total_income > 0 else 0

    return render_template('owner/finance.html',
                           total_income=total_income,
                           total_cost=total_cost,
                           net_profit=net_profit,
                           profit_margin=profit_margin,
                           pending_income=pending_income,
                           monthly_data=monthly_data,
                           all_months=all_months,
                           per_car=per_car,
                           recent_bookings=paid_bookings[:8])


# ─────────────────────────────────────────
# MAINTENANCE ROUTES
# ─────────────────────────────────────────

@app.route('/owner/cars/<int:car_id>/maintenance')
@login_required
@owner_required
def owner_maintenance(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))

    records = car.maintenance_records  # already ordered by date desc
    total_cost = car.total_maintenance_cost

    RTYPES = ['oil_change', 'tire_change', 'full_service', 'repair', 'other']
    cost_by_type = {rt: 0 for rt in RTYPES}
    # stacked: {month: {rtype: cost}}
    cost_stacked = {}
    for r in records:
        rtype = r.record_type if r.record_type in RTYPES else 'other'
        cost_by_type[rtype] = cost_by_type.get(rtype, 0) + (r.cost or 0)
        key = r.service_date.strftime('%Y-%m')
        if key not in cost_stacked:
            cost_stacked[key] = {rt: 0 for rt in RTYPES}
        cost_stacked[key][rtype] = cost_stacked[key].get(rtype, 0) + (r.cost or 0)
    cost_stacked = dict(sorted(cost_stacked.items()))
    cost_by_type = {k: v for k, v in cost_by_type.items() if v > 0}

    # Monthly totals for the table
    monthly_totals = {m: sum(v.values()) for m, v in cost_stacked.items()}

    return render_template('owner/maintenance.html',
                           car=car, records=records, total_cost=total_cost,
                           cost_by_type=cost_by_type,
                           cost_stacked=cost_stacked,
                           monthly_totals=monthly_totals,
                           rtypes=RTYPES,
                           today_date=date.today())


@app.route('/owner/cars/<int:car_id>/maintenance/add', methods=['GET', 'POST'])
@login_required
@owner_required
def owner_maintenance_add(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))

    if request.method == 'POST':
        try:
            km = int(request.form.get('km_at_service', 0) or 0)
            cost = float(request.form.get('cost', 0) or 0)
            next_km = request.form.get('next_service_km', '').strip()
            next_km = int(next_km) if next_km else None
            svc_date = date.fromisoformat(request.form['service_date'])
        except (ValueError, KeyError):
            flash('Invalid values. Please check the form.', 'danger')
            return redirect(url_for('owner_maintenance_add', car_id=car_id))

        rtype = request.form.get('record_type', 'other')

        record = MaintenanceRecord(
            car_id=car.id,
            record_type=rtype,
            title=request.form.get('title', '').strip() or _default_title(rtype),
            service_date=svc_date,
            km_at_service=km if km else None,
            cost=cost,
            description=request.form.get('description', '').strip(),
            next_service_km=next_km,
        )
        db.session.add(record)

        # Update car current KM if higher than stored
        if km and (car.current_km is None or km > car.current_km):
            car.current_km = km

        # If this is a tire change, flush first so last_tire_change_record picks it up, then recalculate
        if rtype == 'tire_change':
            life = request.form.get('tire_life_km', '').strip()
            if life:
                try:
                    car.tire_life_km = int(life)
                except ValueError:
                    pass
            db.session.flush()
            _auto_update_tires(car)

        db.session.commit()
        flash('Maintenance record added.', 'success')
        return redirect(url_for('owner_maintenance', car_id=car_id))

    today = date.today().isoformat()
    return render_template('owner/maintenance_add.html', car=car, today=today)


def _default_title(rtype):
    return {'oil_change': 'Oil Change', 'tire_change': 'Tire Change',
            'full_service': 'Full Service', 'repair': 'Repair', 'other': 'Maintenance'}.get(rtype, 'Maintenance')


def _auto_update_tires(car):
    """Recalculate all 4 tire conditions from KM driven since last tire change.
    Formula: tire_% = 100 - (km_since_change / tire_life_km * 100)
    """
    pct = car.auto_tire_pct
    if pct is not None:
        car.tire_fl = car.tire_fr = car.tire_rl = car.tire_rr = pct


@app.route('/owner/cars/<int:car_id>/maintenance/delete/<int:record_id>', methods=['POST'])
@login_required
@owner_required
def owner_maintenance_delete(car_id, record_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))
    record = MaintenanceRecord.query.get_or_404(record_id)
    if record.car_id != car.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_maintenance', car_id=car_id))
    db.session.delete(record)
    db.session.commit()
    flash('Record deleted.', 'info')
    return redirect(url_for('owner_maintenance', car_id=car_id))


@app.route('/owner/cars/<int:car_id>/maintenance/smoke-test', methods=['POST'])
@login_required
@owner_required
def owner_smoke_test_update(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))
    try:
        td = request.form.get('smoke_test_date', '').strip()
        te = request.form.get('smoke_test_expiry', '').strip()
        if td:
            car.smoke_test_date   = date.fromisoformat(td)
        if te:
            car.smoke_test_expiry = date.fromisoformat(te)
        db.session.commit()
        flash('Smoke test updated.', 'success')
    except ValueError:
        flash('Invalid date format.', 'danger')
    return redirect(url_for('owner_maintenance', car_id=car_id))


@app.route('/owner/cars/<int:car_id>/update-km', methods=['POST'])
@login_required
@owner_required
def owner_update_km(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))
    try:
        car.current_km = int(request.form.get('current_km', car.current_km or 0))
        car.oil_change_interval_km = int(request.form.get('oil_interval', 5000))
        car.service_interval_km = int(request.form.get('service_interval', 10000))
        car.tire_life_km = int(request.form.get('tire_life_km', car.tire_life_km or 40000))
    except ValueError:
        flash('Invalid KM values.', 'danger')
        return redirect(url_for('owner_maintenance', car_id=car_id))
    # Auto-recalculate tire condition from new KM
    _auto_update_tires(car)
    db.session.commit()
    flash('KM updated — tire condition auto-calculated.', 'success')
    return redirect(url_for('owner_maintenance', car_id=car_id))


@app.route('/owner/cars/<int:car_id>/update-tires', methods=['POST'])
@login_required
@owner_required
def owner_update_tires(car_id):
    car = Car.query.get_or_404(car_id)
    if car.owner_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('owner_cars'))
    try:
        car.tire_fl = max(0, min(100, int(request.form.get('tire_fl', 100))))
        car.tire_fr = max(0, min(100, int(request.form.get('tire_fr', 100))))
        car.tire_rl = max(0, min(100, int(request.form.get('tire_rl', 100))))
        car.tire_rr = max(0, min(100, int(request.form.get('tire_rr', 100))))
    except ValueError:
        flash('Invalid tire values.', 'danger')
        return redirect(url_for('owner_maintenance', car_id=car_id))
    db.session.commit()
    flash('Tire conditions updated.', 'success')
    return redirect(url_for('owner_maintenance', car_id=car_id))


# ─────────────────────────────────────────
# SEED & INIT
# ─────────────────────────────────────────

def run_migrations():
    """Add new columns to existing tables without dropping data."""
    with db.engine.connect() as conn:
        car_cols = {row[1] for row in conn.execute(text('PRAGMA table_info(car)')).fetchall()}
        new_cols = {
            'current_km': 'INTEGER DEFAULT 0',
            'oil_change_interval_km': 'INTEGER DEFAULT 5000',
            'service_interval_km': 'INTEGER DEFAULT 10000',
            'tire_fl': 'INTEGER DEFAULT 100',
            'tire_fr': 'INTEGER DEFAULT 100',
            'tire_rl': 'INTEGER DEFAULT 100',
            'tire_rr': 'INTEGER DEFAULT 100',
            'tire_life_km': 'INTEGER DEFAULT 40000',
            'smoke_test_date': 'DATE',
            'smoke_test_expiry': 'DATE',
        }
        for col, typedef in new_cols.items():
            if col not in car_cols:
                conn.execute(text(f'ALTER TABLE car ADD COLUMN {col} {typedef}'))
        conn.commit()


def seed_data():
    if User.query.first():
        return
    owner = User(name='Nuwan Silva', email='owner@demo.com',
                 password_hash=generate_password_hash('demo123'), role='owner', phone='0771234567')
    customer = User(name='Kasun Perera', email='customer@demo.com',
                    password_hash=generate_password_hash('demo123'), role='customer', phone='0712345678')
    db.session.add_all([owner, customer])
    db.session.flush()

    cars = [
        Car(owner_id=owner.id, make='Toyota', model='Prius', year=2022,
            price_per_day=6500, location='Colombo', seats=5, transmission='auto',
            fuel_type='petrol', description='Fuel efficient hybrid. Perfect for city drives.',
            image_url='https://images.unsplash.com/photo-1559416523-140ddc3d238c?w=600&q=80',
            current_km=45200, oil_change_interval_km=5000, service_interval_km=10000,
            tire_fl=85, tire_fr=80, tire_rl=72, tire_rr=68),
        Car(owner_id=owner.id, make='Honda', model='Vezel', year=2021,
            price_per_day=7500, location='Kandy', seats=5, transmission='auto',
            fuel_type='petrol', description='Sporty SUV with great road presence.',
            image_url='https://images.unsplash.com/photo-1606664515524-ed2f786a0bd6?w=600&q=80',
            current_km=62000, oil_change_interval_km=5000, service_interval_km=10000,
            tire_fl=55, tire_fr=50, tire_rl=45, tire_rr=40),
        Car(owner_id=owner.id, make='Suzuki', model='Alto', year=2023,
            price_per_day=3500, location='Galle', seats=4, transmission='manual',
            fuel_type='petrol', description='Budget friendly, easy to park, great fuel economy.',
            image_url='https://images.unsplash.com/photo-1541899481282-d53bffe3c35d?w=600&q=80',
            current_km=12500, oil_change_interval_km=5000, service_interval_km=10000,
            tire_fl=95, tire_fr=95, tire_rl=90, tire_rr=90),
    ]
    db.session.add_all(cars)
    db.session.flush()

    from datetime import timedelta
    today = date.today()
    sample_records = [
        MaintenanceRecord(car_id=cars[0].id, record_type='oil_change', title='Oil Change',
                          service_date=today - timedelta(days=45), km_at_service=42000,
                          cost=3500, next_service_km=47000, description='Castrol 5W-30 synthetic'),
        MaintenanceRecord(car_id=cars[0].id, record_type='full_service', title='Full Service',
                          service_date=today - timedelta(days=90), km_at_service=40000,
                          cost=18000, next_service_km=50000, description='Full service at Toyota dealer'),
        MaintenanceRecord(car_id=cars[1].id, record_type='oil_change', title='Oil Change',
                          service_date=today - timedelta(days=30), km_at_service=60000,
                          cost=4000, next_service_km=65000),
        MaintenanceRecord(car_id=cars[1].id, record_type='tire_change', title='Rear Tires Replaced',
                          service_date=today - timedelta(days=120), km_at_service=55000,
                          cost=28000, description='Bridgestone Turanza x2'),
        MaintenanceRecord(car_id=cars[1].id, record_type='repair', title='AC Compressor Repair',
                          service_date=today - timedelta(days=60), km_at_service=58000, cost=15000),
        MaintenanceRecord(car_id=cars[2].id, record_type='oil_change', title='Oil Change',
                          service_date=today - timedelta(days=10), km_at_service=12000,
                          cost=2500, next_service_km=17000),
    ]
    db.session.add_all(sample_records)
    db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        run_migrations()
        seed_data()
    app.run(debug=True, port=5000)
