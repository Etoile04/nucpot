-- NFM-2012: Reset service account passwords (P0 HOTFIX)
-- Generated: 2026-07-30
--
-- These 3 service accounts had their plaintext passwords lost when the
-- agent run ended.  This script resets them to new known passwords.
--
-- EXECUTE ONLY ONCE.  Re-running is harmless (idempotent SET).
--
-- Passwords have been posted in a Paperclip comment on NFM-2012 for CPO relay.

BEGIN;

UPDATE users
SET hashed_password = '$2b$12$UCrJ8APEFZzxEddAM7ErfuhknpdlJY1vJ62fW1m3AqBTddRDq.aZO'
WHERE username = 'ontofuel-svc'
  AND is_service_account = true;

UPDATE users
SET hashed_password = '$2b$12$sx429Km82xuSDgH6gMTNd.ZKfxMpNtH7vm6LmuX9zZ4XDjy70otaq'
WHERE username = 'ontofuel-svc-e2e'
  AND is_service_account = true;

UPDATE users
SET hashed_password = '$2b$12$YpJxEI5T.U8eVnG..cN65OAKy5lPHLyWEhLJ9AvcrzUips06u4qqy'
WHERE username = 'ontofuel-svc-h199'
  AND is_service_account = true;

COMMIT;
