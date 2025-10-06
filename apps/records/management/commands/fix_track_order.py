# apps/records/management/commands/fix_track_order.py
from django.core.management.base import BaseCommand
from django.db import transaction
from records.models import Record, Track

class Command(BaseCommand):
    help = "Назначает position_index=1..N для треков, где 0/NULL."

    def add_arguments(self, parser):
        parser.add_argument("--record-id", type=int)

    @transaction.atomic
    def handle(self, *args, **opts):
        qs = Record.objects.all()
        if opts.get("record_id"):
            qs = qs.filter(pk=opts["record_id"])
        fixed = 0
        for rec in qs.iterator():
            tracks = list(rec.tracks.order_by("id"))
            need = [t for t in tracks if not t.position_index or t.position_index < 1]
            if not need:
                continue
            for i, t in enumerate(tracks, start=1):
                t.position_index = i
            Track.objects.bulk_update(tracks, ["position_index"])
            fixed += len(need)
            self.stdout.write(f"Record {rec.id}: fixed {len(need)}")
        self.stdout.write(self.style.SUCCESS(f"Done. Fixed: {fixed}"))
