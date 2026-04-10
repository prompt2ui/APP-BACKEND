-- -----------------------------------------------------------------------------
-- Align legacy "Provider" tables with Prisma: enum ProviderType, column provider_type,
-- unique (user_id, provider_type). Run in Supabase SQL Editor.
--
-- Handles: (a) old provider_name only, (b) kind + ProviderKind from earlier migration,
-- (c) already correct — mostly no-ops.
-- -----------------------------------------------------------------------------

-- A) Rename ProviderKind → ProviderType if needed
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ProviderKind')
     AND NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ProviderType') THEN
    ALTER TYPE "ProviderKind" RENAME TO "ProviderType";
  END IF;
END $$;

-- B) Rename kind → provider_type if needed
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'Provider' AND column_name = 'kind'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'Provider' AND column_name = 'provider_type'
  ) THEN
    ALTER TABLE "Provider" RENAME COLUMN "kind" TO "provider_type";
  END IF;
END $$;

-- C) Drop old unique constraint name if present
ALTER TABLE "Provider" DROP CONSTRAINT IF EXISTS "Provider_user_id_kind_key";

-- 1) Enum (idempotent)
DO $$ BEGIN
  CREATE TYPE "ProviderType" AS ENUM ('github', 'clickup', 'jira');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 2) Add provider_type if still missing
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'Provider'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'Provider' AND column_name = 'provider_type'
  ) THEN
    ALTER TABLE "Provider" ADD COLUMN "provider_type" "ProviderType";
  END IF;
END $$;

-- 3) Backfill from legacy provider_name (if present)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'Provider' AND column_name = 'provider_name'
  ) THEN
    UPDATE "Provider" SET "provider_type" = CASE
      WHEN LOWER(TRIM(COALESCE("provider_name", ''))) IN ('github', 'gh') THEN 'github'::"ProviderType"
      WHEN LOWER(TRIM(COALESCE("provider_name", ''))) IN ('clickup', 'click_up') THEN 'clickup'::"ProviderType"
      WHEN LOWER(TRIM(COALESCE("provider_name", ''))) IN ('jira') THEN 'jira'::"ProviderType"
      ELSE 'github'::"ProviderType"
    END
    WHERE "provider_type" IS NULL;
    ALTER TABLE "Provider" DROP COLUMN "provider_name";
  END IF;
END $$;

-- 4) NULL → default
UPDATE "Provider" SET "provider_type" = 'github'::"ProviderType" WHERE "provider_type" IS NULL;

-- 5) Dedupe
DELETE FROM "Provider" p
USING "Provider" p2
WHERE p.user_id = p2.user_id
  AND p.provider_type = p2.provider_type
  AND p.provider_id > p2.provider_id;

-- 6) NOT NULL + unique
ALTER TABLE "Provider" ALTER COLUMN "provider_type" SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'Provider_user_id_provider_type_key'
  ) THEN
    ALTER TABLE "Provider" ADD CONSTRAINT "Provider_user_id_provider_type_key" UNIQUE ("user_id", "provider_type");
  END IF;
END $$;

-- 7) Timestamps
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'Provider'
  ) THEN
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'Provider' AND column_name = 'created_at'
    ) THEN
      ALTER TABLE "Provider" ADD COLUMN "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW();
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'Provider' AND column_name = 'updated_at'
    ) THEN
      ALTER TABLE "Provider" ADD COLUMN "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW();
    END IF;
  END IF;
END $$;

-- 8) provider_config → jsonb
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'Provider'
      AND column_name = 'provider_config' AND data_type IN ('text', 'character varying')
  ) THEN
    ALTER TABLE "Provider"
      ALTER COLUMN "provider_config" TYPE jsonb USING COALESCE("provider_config"::jsonb, '{}'::jsonb);
  END IF;
END $$;
