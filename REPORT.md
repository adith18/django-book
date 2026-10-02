# BookMySeat — Project Report

## Admin credentials

Run once after migrations:

```bash
python manage.py ensure_admin
```

| Field | Value |
|--------|--------|
| **Username** | `bookmyseat_admin` |
| **Password** | `BookMySeat@2026!` |
| **Email** | `admin@bookmyseat.local` |

Access:

- Django Admin: `/admin/`
- Custom admin dashboard (analytics + scheduling): `/movies/custom-admin/`

## Feature overview

| Module | Implementation |
|--------|----------------|
| Movie management | Django Admin + custom scheduler (`/movies/custom-admin/movie/.../schedule/`), genres, languages, cast, posters, YouTube-nocookie trailers, certifications |
| Reviews | Post-watch only, edit via form, report flow, verified viewer badge, staff moderation |
| Seat reservation | 2-minute holds, live status API, `select_for_update` + transactions |
| Payments | Razorpay (production) or **mock checkout** when keys are unset; webhooks; idempotent verify |
| Tickets | PDF + QR via ReportLab; email via Celery (`CELERY_TASK_ALWAYS_EAGER=True` in dev) |
| Discovery | Search, filters (genre, language, city, theater, dates, rating, show times), sort, pagination, match count, “Recommended for You” |
| Analytics | ORM aggregations, date range filter, CSV export on custom admin dashboard |

## Environment variables

```env
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_TASK_ALWAYS_EAGER=False
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DEFAULT_FROM_EMAIL=noreply@yourdomain.com
```

Without Razorpay keys, **Confirm & Pay** uses mock verification (suitable for demos and tests).

## Query performance (100k+ bookings)

Indexes added on high-cardinality filter/join columns:

- `Booking`: `(show_time, seat)`, `(user, booked_at)`, `(theater, booked_at)`, `booked_at`
- `PaymentTransaction`: `(status, created_at)`, `(user, created_at)`, `provider_order_id`
- `BookingOrder`: `(user, created_at)`, `reference`
- `Theater.city`, `MovieView (user, viewed_at)`

Analytics uses `Sum`, `Count`, `TruncDate`, and `ExtractHour` in the database — no Python-side iteration over booking rows. For PostgreSQL at scale, prefer the commented Render Postgres URL in `settings.py` and run `EXPLAIN ANALYZE` on dashboard queries; composite indexes on `(status, created_at)` for payments and `(show_time_id, seat_id)` for bookings keep seat conflict checks and revenue reports index-only friendly.

**Expected improvements:** seat availability checks avoid full table scans on `Booking` by filtering on `show_time_id` (indexed FK). Revenue reports filter `PaymentTransaction` by `status` + `created_at` using the composite index instead of loading all successful payments into memory.

## Local setup

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py ensure_admin
python manage.py runserver
```

Optional worker (production email):

```bash
celery -A bookmyseat worker -l info
```

## Running tests

```bash
python manage.py test movies
```
