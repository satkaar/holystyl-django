from rest_framework import serializers

from .models import Task, TeamMember


class TeamMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamMember
        # La fiche salarié (état civil, NIR, adresse) ne sort pas par l'API :
        # elle ne sert qu'au contrat, et se saisit là où le NIR est vérifié.
        exclude = ["exploitation", "location_token", "location_token_expires_at",
                   "civilite", "nom_naissance", "date_naissance", "lieu_naissance",
                   "nationalite", "numero_securite_sociale", "adresse", "code_postal",
                   "ville", "titre_sejour_numero", "titre_sejour_expire_le"]
        read_only_fields = ["id", "is_online", "last_seen_at", "created_at", "updated_at"]


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        exclude = ["exploitation"]
        read_only_fields = ["id", "created_at", "updated_at",
                            "reminder_sent_24h", "reminder_sent_1h", "sms_sent_24h", "sms_sent_1h"]
