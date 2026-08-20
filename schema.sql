CREATE DATABASE IF NOT EXISTS hgw_call_logs
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE hgw_call_logs;

CREATE TABLE IF NOT EXISTS call_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_hash CHAR(64) NOT NULL,
  observed_at DATETIME NOT NULL,
  call_direction VARCHAR(32) NULL,
  displayed_number VARCHAR(128) NULL,
  call_date_time VARCHAR(64) NULL,
  started_at_text VARCHAR(64) NULL,
  ended_at_text VARCHAR(64) NULL,
  device_phone_number VARCHAR(128) NULL,
  ip_terminal_address VARCHAR(64) NULL,
  media_type VARCHAR(32) NULL,
  extension_number VARCHAR(32) NULL,
  peer_phone_number VARCHAR(128) NULL,
  peer_ip_address VARCHAR(64) NULL,
  disconnect_source VARCHAR(64) NULL,
  disconnect_reason VARCHAR(128) NULL,
  sip_disconnect_reason VARCHAR(128) NULL,
  channel_number VARCHAR(64) NULL,
  raw_record JSON NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_call_logs_source_hash (source_hash),
  KEY ix_call_logs_observed_at (observed_at)
) ENGINE=InnoDB;
