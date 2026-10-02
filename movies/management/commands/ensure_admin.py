from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Ensure default admin staff user exists (for project reports / local dev).'

    def handle(self, *args, **options):
        username = 'bookmyseat_admin'
        email = 'admin@bookmyseat.local'
        password = 'BookMySeat@2026!'
        user, created = User.objects.get_or_create(username=username, defaults={'email': email})
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        action = 'Created' if created else 'Updated'
        self.stdout.write(self.style.SUCCESS(f'{action} admin user "{username}" (see REPORT.md for credentials).'))
