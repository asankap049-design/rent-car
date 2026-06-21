from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'customer' or 'owner'
    phone = db.Column(db.String(20))
    address = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    cars = db.relationship('Car', backref='owner', lazy=True, foreign_keys='Car.owner_id')
    bookings = db.relationship('Booking', backref='customer', lazy=True, foreign_keys='Booking.customer_id')


class Car(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    make = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    price_per_day = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(200))
    description = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    seats = db.Column(db.Integer, default=5)
    transmission = db.Column(db.String(20), default='auto')
    fuel_type = db.Column(db.String(20), default='petrol')
    is_available = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Maintenance tracking
    current_km = db.Column(db.Integer, default=0)
    oil_change_interval_km = db.Column(db.Integer, default=5000)
    service_interval_km = db.Column(db.Integer, default=10000)
    # Tire condition 0-100% (FL=Front Left, FR=Front Right, RL=Rear Left, RR=Rear Right)
    tire_fl = db.Column(db.Integer, default=100)
    tire_fr = db.Column(db.Integer, default=100)
    tire_rl = db.Column(db.Integer, default=100)
    tire_rr = db.Column(db.Integer, default=100)
    tire_life_km = db.Column(db.Integer, default=40000)  # full tire lifespan in KM

    # Smoke test (emission test) tracking
    smoke_test_date   = db.Column(db.Date)
    smoke_test_expiry = db.Column(db.Date)

    bookings = db.relationship('Booking', backref='car', lazy=True)
    maintenance_records = db.relationship('MaintenanceRecord', backref='vehicle', lazy=True,
                                          cascade='all, delete-orphan',
                                          order_by='MaintenanceRecord.service_date.desc()')

    @property
    def active_bookings(self):
        return [b for b in self.bookings if b.status in ('pending', 'confirmed')]

    @property
    def last_tire_change_record(self):
        return next((r for r in self.maintenance_records if r.record_type == 'tire_change'), None)

    @property
    def last_tire_change_km(self):
        r = self.last_tire_change_record
        return r.km_at_service if (r and r.km_at_service) else None

    @property
    def km_since_tire_change(self):
        ltk = self.last_tire_change_km
        if ltk is not None and self.current_km:
            return max(0, self.current_km - ltk)
        return None

    @property
    def km_remaining_on_tires(self):
        kst = self.km_since_tire_change
        if kst is not None and self.tire_life_km:
            return max(0, self.tire_life_km - kst)
        return None

    @property
    def auto_tire_pct(self):
        """Calculate tire % from KM driven since last tire change."""
        kst = self.km_since_tire_change
        if kst is None or not self.tire_life_km:
            return None
        return max(0, round(100 - (kst / self.tire_life_km * 100)))

    @property
    def last_oil_change(self):
        return next((r for r in self.maintenance_records if r.record_type == 'oil_change'), None)

    @property
    def last_service(self):
        return next((r for r in self.maintenance_records if r.record_type == 'full_service'), None)

    @property
    def oil_change_due_km(self):
        """KM when next oil change is due. Returns None if no record."""
        last = self.last_oil_change
        if last and last.km_at_service:
            return last.km_at_service + self.oil_change_interval_km
        return None

    @property
    def service_due_km(self):
        last = self.last_service
        if last and last.km_at_service:
            return last.km_at_service + self.service_interval_km
        return None

    @property
    def oil_alert(self):
        """Returns 'overdue'/'soon'/None based on current KM vs due KM."""
        due = self.oil_change_due_km
        if due is None or self.current_km is None:
            return None
        diff = self.current_km - due
        if diff >= 0:
            return ('overdue', diff)
        if diff >= -500:
            return ('soon', abs(diff))
        return None

    @property
    def service_alert(self):
        due = self.service_due_km
        if due is None or self.current_km is None:
            return None
        diff = self.current_km - due
        if diff >= 0:
            return ('overdue', diff)
        if diff >= -1000:
            return ('soon', abs(diff))
        return None

    @property
    def tire_alerts(self):
        """Returns list of tires with condition below 20%."""
        alerts = []
        for name, val in [('FL', self.tire_fl), ('FR', self.tire_fr),
                           ('RL', self.tire_rl), ('RR', self.tire_rr)]:
            if val is not None and val < 20:
                alerts.append((name, val))
        return alerts

    @property
    def total_maintenance_cost(self):
        return sum(r.cost or 0 for r in self.maintenance_records)

    @property
    def smoke_test_alert(self):
        from datetime import date as _date
        if not self.smoke_test_expiry:
            return None
        days_left = (self.smoke_test_expiry - _date.today()).days
        if days_left < 0:
            return ('overdue', abs(days_left))
        if days_left <= 30:
            return ('soon', days_left)
        return None

    @property
    def has_alerts(self):
        return bool(self.oil_alert or self.service_alert or self.tire_alerts or self.smoke_test_alert)


class MaintenanceRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey('car.id'), nullable=False)
    record_type = db.Column(db.String(30), nullable=False)
    # oil_change / tire_change / full_service / repair / other
    title = db.Column(db.String(200), nullable=False)
    service_date = db.Column(db.Date, nullable=False)
    km_at_service = db.Column(db.Integer)
    cost = db.Column(db.Float, default=0)
    description = db.Column(db.Text)
    next_service_km = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def type_label(self):
        return {
            'oil_change': 'Oil Change',
            'tire_change': 'Tire Change',
            'full_service': 'Full Service',
            'repair': 'Repair',
            'other': 'Other',
        }.get(self.record_type, self.record_type.replace('_', ' ').title())

    @property
    def type_color(self):
        return {
            'oil_change': 'warning',
            'tire_change': 'info',
            'full_service': 'primary',
            'repair': 'danger',
            'other': 'secondary',
        }.get(self.record_type, 'secondary')

    @property
    def type_icon(self):
        return {
            'oil_change': 'bi-droplet-fill',
            'tire_change': 'bi-circle-fill',
            'full_service': 'bi-tools',
            'repair': 'bi-wrench-adjustable-circle-fill',
            'other': 'bi-gear-fill',
        }.get(self.record_type, 'bi-gear-fill')


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey('car.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def days(self):
        return max((self.end_date - self.start_date).days, 1)

    @property
    def status_color(self):
        return {
            'pending': 'warning',
            'confirmed': 'success',
            'rejected': 'danger',
            'completed': 'info',
            'cancelled': 'secondary',
        }.get(self.status, 'secondary')
