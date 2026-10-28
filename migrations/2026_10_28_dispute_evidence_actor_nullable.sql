-- Staff accounts may have no actors row; allow admin-uploaded evidence without actor.
ALTER TABLE dispute_evidence ALTER COLUMN uploaded_by_actor_id DROP NOT NULL;
