-- BondSupermart scraper schema
-- All data lives in its own database: bondsupermart

CREATE DATABASE IF NOT EXISTS bondsupermart
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE bondsupermart;

-- Master bond table. Most fields come from the v4 bond-factsheet detail
-- endpoint (bondFactSheetDisplay); ratings / years_to_maturity / next_call_date
-- are taken from the bond-selector filter list.
CREATE TABLE IF NOT EXISTS bonds (
  isin                VARCHAR(32)  NOT NULL,
  cusip               VARCHAR(32),
  bond_issuer         VARCHAR(255),
  guarantor           VARCHAR(255),
  announcement_date   DATE,
  issue_date          DATE,
  maturity_date       DATE,
  years_to_maturity   DECIMAL(10,3),
  next_call_date      DATE,
  issue_price         DECIMAL(18,6),
  issue_yield         DECIMAL(18,6),
  coupon_type         VARCHAR(32),
  coupon_rate         DECIMAL(18,6),
  coupon_frequency    VARCHAR(16),
  seniority           VARCHAR(64),
  exchange_listed     VARCHAR(32),
  bond_currency       VARCHAR(16),
  total_issue_size    DECIMAL(30,2),
  min_investment      DECIMAL(20,2),
  incremental_quantity DECIMAL(20,2),
  bond_type           VARCHAR(32),
  bond_sector         VARCHAR(64),
  bond_sub_sector     VARCHAR(64),
  sp_rating           VARCHAR(32),
  fitch_rating        VARCHAR(32),
  shariah_compliant   VARCHAR(8),
  sukuk_investing     VARCHAR(8),
  -- Indicative bid/ask snapshot lifted from the filter-list payload
  -- (raw_json $.filter.bondInfo), available for ALL bonds, not just the
  -- exchange-listed set served by bond_prices.
  bid_price           DECIMAL(18,6),
  ask_price           DECIMAL(18,6),
  bid_ytm             DECIMAL(18,6),
  ask_ytm             DECIMAL(18,6),
  bid_ytw             DECIMAL(18,6),
  ask_ytw             DECIMAL(18,6),
  price_updated_at    DATETIME,
  raw_json            LONGTEXT,
  scraped_at          DATETIME,
  PRIMARY KEY (isin),
  KEY idx_issuer (bond_issuer),
  KEY idx_maturity (maturity_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Live exchange prices (Phase 3). Only exchange-listed bonds return data.
CREATE TABLE IF NOT EXISTS bond_prices (
  id                BIGINT NOT NULL AUTO_INCREMENT,
  isin              VARCHAR(32),
  symbol            VARCHAR(64),
  bid_price         DECIMAL(18,6),
  ask_price         DECIMAL(18,6),
  bid_yield         DECIMAL(18,6),
  ask_yield         DECIMAL(18,6),
  change_bid_price  DECIMAL(18,6),
  change_ask_price  DECIMAL(18,6),
  price_timestamp   DATETIME(3),
  scraped_at        DATETIME,
  PRIMARY KEY (id),
  KEY idx_isin (isin),
  KEY idx_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Historical "Since Inception" chart points merged across yield + price series.
CREATE TABLE IF NOT EXISTS bond_chart (
  id                    BIGINT NOT NULL AUTO_INCREMENT,
  isin                  VARCHAR(32),
  chart_date            DATE,
  ask_yield_to_worst    DECIMAL(18,6),
  bid_yield_to_worst    DECIMAL(18,6),
  ask_yield_to_maturity DECIMAL(18,6),
  bid_yield_to_maturity DECIMAL(18,6),
  ask_price             DECIMAL(18,6),
  bid_price             DECIMAL(18,6),
  PRIMARY KEY (id),
  UNIQUE KEY uniq_isin_date (isin, chart_date),
  KEY idx_isin (isin)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Daily indicative price/yield snapshots (one row per bond per day), written
-- by daily_price_update.py from the filter-list payload. Upserted on
-- (isin, price_date) so a same-day re-run refreshes rather than duplicates.
CREATE TABLE IF NOT EXISTS bond_prices_history (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  isin        VARCHAR(32),
  price_date  DATE,
  bid_price   DECIMAL(18,6),
  ask_price   DECIMAL(18,6),
  bid_ytm     DECIMAL(18,6),
  ask_ytm     DECIMAL(18,6),
  bid_ytw     DECIMAL(18,6),
  ask_ytw     DECIMAL(18,6),
  scraped_at  DATETIME,
  UNIQUE KEY uniq_isin_date (isin, price_date),
  KEY idx_isin (isin),
  KEY idx_date (price_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-phase, per-bond progress so the scraper can resume.
CREATE TABLE IF NOT EXISTS scrape_progress (
  phase    VARCHAR(32) NOT NULL,
  isin     VARCHAR(32) NOT NULL,
  done_at  DATETIME,
  PRIMARY KEY (phase, isin)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
