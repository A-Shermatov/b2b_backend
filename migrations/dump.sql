--
-- PostgreSQL database dump
--

\restrict q1Gsfj3cWf5tNgEfQHCYULIbAG8DAgTes99oZFfE4o84XIDRgdUfQREWQfrguQ2

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: actor_kind; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.actor_kind AS ENUM (
    'individual',
    'company'
);


--
-- Name: actor_side; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.actor_side AS ENUM (
    'buyer',
    'supplier'
);


--
-- Name: actor_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.actor_type AS ENUM (
    'buyer',
    'supplier'
);


--
-- Name: budget_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.budget_type AS ENUM (
    'fixed',
    'range',
    'open'
);


--
-- Name: catalog_item_report_reason; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.catalog_item_report_reason AS ENUM (
    'spam',
    'fraud',
    'counterfeit',
    'abuse',
    'other'
);


--
-- Name: catalog_item_report_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.catalog_item_report_status AS ENUM (
    'open',
    'resolved',
    'dismissed'
);


--
-- Name: catalog_item_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.catalog_item_type AS ENUM (
    'product',
    'service'
);


--
-- Name: company_operational_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.company_operational_status AS ENUM (
    'active',
    'blocked',
    'deactivated'
);


--
-- Name: company_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.company_role AS ENUM (
    'director',
    'admin',
    'moderator',
    'accountant'
);


--
-- Name: contract_currency; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.contract_currency AS ENUM (
    'TJS',
    'USD',
    'EUR',
    'KZT',
    'CNY'
);


--
-- Name: contract_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.contract_status AS ENUM (
    'pending_payment',
    'active',
    'delivered',
    'completed',
    'cancelled',
    'disputed'
);


--
-- Name: currency; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.currency AS ENUM (
    'TJS',
    'USD',
    'EUR',
    'KZT',
    'CNY'
);


--
-- Name: dispute_resolution; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.dispute_resolution AS ENUM (
    'release_funds',
    'refund_buyer',
    'partial_refund',
    'close_case'
);


--
-- Name: dispute_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.dispute_status AS ENUM (
    'open',
    'under_review',
    'resolved',
    'appealed'
);


--
-- Name: invoice_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.invoice_status AS ENUM (
    'draft',
    'issued',
    'paid',
    'overdue',
    'cancelled'
);


--
-- Name: item_media_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.item_media_type AS ENUM (
    'image',
    'document',
    'video'
);


--
-- Name: item_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.item_status AS ENUM (
    'draft',
    'pending_review',
    'changes_requested',
    'active',
    'hidden',
    'archived',
    'deleted'
);


--
-- Name: message_delivery_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.message_delivery_status AS ENUM (
    'sent',
    'delivered',
    'viewed'
);


--
-- Name: milestone_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.milestone_status AS ENUM (
    'pending',
    'awaiting_payment',
    'funded',
    'in_progress',
    'submitted',
    'approved',
    'released',
    'refunded',
    'disputed',
    'overdue',
    'cancelled'
);


--
-- Name: notification_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.notification_type AS ENUM (
    'order',
    'offer',
    'payment',
    'system',
    'rfq',
    'contract',
    'proposal'
);


--
-- Name: payment_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.payment_type AS ENUM (
    'full_prepayment',
    'split_payment',
    'milestone',
    'full_postpayment'
);


--
-- Name: plan_payment_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.plan_payment_type AS ENUM (
    'full_prepayment',
    'split_payment',
    'milestone',
    'full_postpayment'
);


--
-- Name: platform_payment_gateway; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.platform_payment_gateway AS ENUM (
    'manual',
    'mock',
    'stripe',
    'yookassa'
);


--
-- Name: platform_payment_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.platform_payment_status AS ENUM (
    'pending',
    'processing',
    'paid',
    'failed',
    'refunded',
    'cancelled'
);


--
-- Name: platform_payment_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.platform_payment_type AS ENUM (
    'platform_revenue',
    'subscription',
    'commission',
    'refund',
    'payout'
);


--
-- Name: pricing_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.pricing_type AS ENUM (
    'fixed',
    'tiered',
    'hourly',
    'monthly'
);


--
-- Name: proposal_report_reason; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.proposal_report_reason AS ENUM (
    'spam',
    'fraud',
    'counterfeit',
    'abuse',
    'other'
);


--
-- Name: proposal_report_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.proposal_report_status AS ENUM (
    'open',
    'resolved',
    'dismissed'
);


--
-- Name: proposal_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.proposal_status AS ENUM (
    'submitted',
    'viewed',
    'shortlisted',
    'accepted',
    'rejected',
    'withdrawn'
);


--
-- Name: rfq_report_reason; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.rfq_report_reason AS ENUM (
    'spam',
    'fraud',
    'counterfeit',
    'abuse',
    'other'
);


--
-- Name: rfq_report_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.rfq_report_status AS ENUM (
    'open',
    'resolved',
    'dismissed'
);


--
-- Name: rfq_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.rfq_status AS ENUM (
    'draft',
    'published',
    'receiving_proposals',
    'supplier_selected',
    'contract_created',
    'in_progress',
    'completed',
    'cancelled',
    'expired',
    'disputed',
    'archived'
);


--
-- Name: rfq_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.rfq_type AS ENUM (
    'product',
    'service'
);


--
-- Name: rfq_visibility; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.rfq_visibility AS ENUM (
    'public',
    'invited_only'
);


--
-- Name: submission_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.submission_status AS ENUM (
    'pending',
    'accepted',
    'rejected'
);


--
-- Name: submission_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.submission_type AS ENUM (
    'delivery',
    'work'
);


--
-- Name: subscription_plan; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.subscription_plan AS ENUM (
    'none',
    'start',
    'pro',
    'business'
);


--
-- Name: trust_level; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.trust_level AS ENUM (
    'basic',
    'standard',
    'verified'
);


--
-- Name: user_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.user_role AS ENUM (
    'buyer',
    'supplier',
    'both',
    'admin',
    'superadmin',
    'moderator'
);


--
-- Name: user_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.user_status AS ENUM (
    'pending',
    'active',
    'blocked'
);


--
-- Name: verification_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.verification_status AS ENUM (
    'pending',
    'verified',
    'rejected',
    'needs_documents'
);


--
-- Name: withdrawal_destination_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.withdrawal_destination_type AS ENUM (
    'bank',
    'wallet'
);


--
-- Name: withdrawal_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.withdrawal_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed',
    'cancelled'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: actors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actors (
    id integer NOT NULL,
    kind public.actor_kind NOT NULL,
    side public.actor_side NOT NULL,
    user_id integer,
    company_id integer,
    display_name character varying(255) NOT NULL,
    trust_level public.trust_level NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: actors_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.actors_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: actors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.actors_id_seq OWNED BY public.actors.id;


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id integer NOT NULL,
    user_id integer,
    action character varying(100) NOT NULL,
    resource_type character varying(100) NOT NULL,
    resource_id character varying(100) NOT NULL,
    details jsonb,
    ip_address character varying(50),
    created_at timestamp with time zone NOT NULL
);


--
-- Name: audit_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_logs_id_seq OWNED BY public.audit_logs.id;


--
-- Name: catalog_item_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_item_reports (
    id integer NOT NULL,
    item_id integer NOT NULL,
    reporter_user_id integer NOT NULL,
    reason public.catalog_item_report_reason NOT NULL,
    details text,
    status public.catalog_item_report_status NOT NULL,
    created_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_id integer
);


--
-- Name: catalog_item_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.catalog_item_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: catalog_item_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.catalog_item_reports_id_seq OWNED BY public.catalog_item_reports.id;


--
-- Name: catalog_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_items (
    id integer NOT NULL,
    actor_id integer NOT NULL,
    type public.catalog_item_type NOT NULL,
    category_id integer NOT NULL,
    title character varying(255) NOT NULL,
    description text,
    status public.item_status NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: catalog_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.catalog_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: catalog_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.catalog_items_id_seq OWNED BY public.catalog_items.id;


--
-- Name: categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.categories (
    id integer NOT NULL,
    parent_id integer,
    name character varying(255) NOT NULL,
    slug character varying(100) NOT NULL
);


--
-- Name: categories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.categories_id_seq OWNED BY public.categories.id;


--
-- Name: companies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.companies (
    id integer NOT NULL,
    title character varying(255) NOT NULL,
    actor_type public.actor_type NOT NULL,
    owner_id integer NOT NULL,
    legal_name character varying(255),
    tax_number character varying(50),
    website character varying(500),
    description text,
    logo character varying(500),
    country character varying(100),
    city character varying(100),
    address character varying(500),
    verification_status public.verification_status NOT NULL,
    operational_status public.company_operational_status NOT NULL,
    rating double precision NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: companies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.companies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: companies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.companies_id_seq OWNED BY public.companies.id;


--
-- Name: company_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_categories (
    id integer NOT NULL,
    company_id integer NOT NULL,
    category_id integer NOT NULL
);


--
-- Name: company_categories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.company_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: company_categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.company_categories_id_seq OWNED BY public.company_categories.id;


--
-- Name: company_certificates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_certificates (
    id integer NOT NULL,
    company_id integer NOT NULL,
    title character varying(255) NOT NULL,
    issuer character varying(255) NOT NULL,
    issue_date character varying(20) NOT NULL,
    expiry_date character varying(20),
    file_url character varying(500) NOT NULL
);


--
-- Name: company_certificates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.company_certificates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: company_certificates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.company_certificates_id_seq OWNED BY public.company_certificates.id;


--
-- Name: company_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_profiles (
    company_id integer NOT NULL,
    founded_year integer,
    employees_count integer,
    annual_revenue_range character varying(100),
    languages character varying[] NOT NULL,
    industries character varying[] NOT NULL
);


--
-- Name: company_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_stats (
    company_id integer NOT NULL,
    completed_contracts integer NOT NULL,
    active_contracts integer NOT NULL,
    disputes_count integer NOT NULL,
    average_rating double precision NOT NULL
);


--
-- Name: company_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_users (
    id integer NOT NULL,
    company_id integer NOT NULL,
    user_id integer NOT NULL,
    role public.company_role NOT NULL
);


--
-- Name: company_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.company_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: company_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.company_users_id_seq OWNED BY public.company_users.id;


--
-- Name: contract_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contract_files (
    id integer NOT NULL,
    contract_id integer NOT NULL,
    file_name character varying(255) NOT NULL,
    file_url character varying(500) NOT NULL,
    file_type character varying(100) NOT NULL,
    uploaded_by integer NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: contract_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contract_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contract_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contract_files_id_seq OWNED BY public.contract_files.id;


--
-- Name: contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contracts (
    id integer NOT NULL,
    rfq_id character varying(36) NOT NULL,
    proposal_id integer NOT NULL,
    buyer_actor_id integer NOT NULL,
    supplier_actor_id integer NOT NULL,
    title character varying(500) NOT NULL,
    description text,
    agreed_amount double precision NOT NULL,
    currency public.contract_currency NOT NULL,
    start_date character varying(30) NOT NULL,
    due_date character varying(30) NOT NULL,
    payment_type public.payment_type NOT NULL,
    status public.contract_status NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: contracts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contracts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contracts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contracts_id_seq OWNED BY public.contracts.id;


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id integer NOT NULL,
    contract_id integer NOT NULL
);


--
-- Name: conversations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversations_id_seq OWNED BY public.conversations.id;


--
-- Name: dispute_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dispute_evidence (
    id integer NOT NULL,
    dispute_id integer NOT NULL,
    uploaded_by_actor_id integer NOT NULL,
    file_name character varying(255) NOT NULL,
    file_url character varying(500) NOT NULL,
    file_type character varying(100) NOT NULL,
    note text,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: dispute_evidence_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dispute_evidence_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dispute_evidence_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dispute_evidence_id_seq OWNED BY public.dispute_evidence.id;


--
-- Name: disputes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.disputes (
    id integer NOT NULL,
    contract_id integer NOT NULL,
    status public.dispute_status NOT NULL,
    opened_by_actor_id integer,
    buyer_statement text,
    supplier_statement text,
    admin_instructions text,
    resolution public.dispute_resolution,
    resolution_note text,
    partial_buyer_amount double precision,
    resolved_at timestamp with time zone,
    resolved_by_id integer,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: disputes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.disputes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: disputes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.disputes_id_seq OWNED BY public.disputes.id;


--
-- Name: item_attributes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_attributes (
    id integer NOT NULL,
    item_id integer NOT NULL,
    name character varying(255) NOT NULL,
    value character varying(500) NOT NULL,
    value_type character varying(50) NOT NULL,
    sort_order integer NOT NULL
);


--
-- Name: item_attributes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.item_attributes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: item_attributes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.item_attributes_id_seq OWNED BY public.item_attributes.id;


--
-- Name: item_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_media (
    id integer NOT NULL,
    item_id integer NOT NULL,
    file_name character varying(255) NOT NULL,
    file_url character varying(500) NOT NULL,
    media_type public.item_media_type NOT NULL,
    sort_order integer NOT NULL
);


--
-- Name: item_media_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.item_media_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: item_media_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.item_media_id_seq OWNED BY public.item_media.id;


--
-- Name: item_pricing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_pricing (
    id integer NOT NULL,
    item_id integer NOT NULL,
    pricing_type public.pricing_type NOT NULL,
    currency character varying(10) NOT NULL,
    fixed_price double precision,
    hourly_rate double precision,
    monthly_rate double precision,
    tiers jsonb NOT NULL
);


--
-- Name: item_pricing_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.item_pricing_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: item_pricing_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.item_pricing_id_seq OWNED BY public.item_pricing.id;


--
-- Name: item_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_stats (
    id integer NOT NULL,
    item_id integer NOT NULL,
    views integer NOT NULL,
    leads integer NOT NULL
);


--
-- Name: item_stats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.item_stats_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: item_stats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.item_stats_id_seq OWNED BY public.item_stats.id;


--
-- Name: message_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.message_attachments (
    id integer NOT NULL,
    message_id integer NOT NULL,
    file_name character varying(255) NOT NULL,
    file_url character varying(500) NOT NULL,
    file_type character varying(100) NOT NULL
);


--
-- Name: message_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.message_attachments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: message_attachments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.message_attachments_id_seq OWNED BY public.message_attachments.id;


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id integer NOT NULL,
    conversation_id integer NOT NULL,
    sender_id integer NOT NULL,
    text text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    status public.message_delivery_status NOT NULL,
    delivered_at timestamp with time zone,
    viewed_at timestamp with time zone
);


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id integer NOT NULL,
    user_id integer NOT NULL,
    company_id integer,
    type public.notification_type NOT NULL,
    title character varying(255) NOT NULL,
    body text NOT NULL,
    href character varying(500),
    read boolean NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: payment_milestones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_milestones (
    id integer NOT NULL,
    payment_plan_id integer NOT NULL,
    contract_id integer NOT NULL,
    title character varying(255) NOT NULL,
    percentage double precision NOT NULL,
    amount double precision NOT NULL,
    trigger character varying(50) NOT NULL,
    status public.milestone_status NOT NULL
);


--
-- Name: payment_milestones_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.payment_milestones_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: payment_milestones_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.payment_milestones_id_seq OWNED BY public.payment_milestones.id;


--
-- Name: payment_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_plans (
    id integer NOT NULL,
    contract_id integer NOT NULL,
    payment_type public.plan_payment_type NOT NULL
);


--
-- Name: payment_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.payment_plans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: payment_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.payment_plans_id_seq OWNED BY public.payment_plans.id;


--
-- Name: platform_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.platform_payments (
    id integer NOT NULL,
    external_id character varying(100),
    type public.platform_payment_type NOT NULL,
    status public.platform_payment_status NOT NULL,
    gateway public.platform_payment_gateway NOT NULL,
    amount double precision NOT NULL,
    commission double precision NOT NULL,
    currency character varying(10) NOT NULL,
    title character varying(255) NOT NULL,
    description text,
    actor_id integer,
    invoice_id integer,
    withdrawal_id integer,
    contract_id integer,
    subscription_user_id integer,
    metadata jsonb,
    paid_at timestamp with time zone,
    failed_at timestamp with time zone,
    refunded_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: platform_payments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.platform_payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: platform_payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.platform_payments_id_seq OWNED BY public.platform_payments.id;


--
-- Name: proposal_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proposal_attachments (
    id integer NOT NULL,
    proposal_id integer NOT NULL,
    file_name character varying(255) NOT NULL,
    file_url character varying(500) NOT NULL,
    file_type character varying(100) NOT NULL
);


--
-- Name: proposal_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.proposal_attachments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: proposal_attachments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.proposal_attachments_id_seq OWNED BY public.proposal_attachments.id;


--
-- Name: proposal_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proposal_messages (
    id integer NOT NULL,
    proposal_id integer NOT NULL,
    sender_id integer NOT NULL,
    text text NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: proposal_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.proposal_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: proposal_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.proposal_messages_id_seq OWNED BY public.proposal_messages.id;


--
-- Name: proposal_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proposal_reports (
    id integer NOT NULL,
    proposal_id integer NOT NULL,
    reporter_user_id integer NOT NULL,
    reason public.proposal_report_reason NOT NULL,
    details text,
    status public.proposal_report_status NOT NULL,
    created_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_id integer
);


--
-- Name: proposal_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.proposal_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: proposal_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.proposal_reports_id_seq OWNED BY public.proposal_reports.id;


--
-- Name: proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.proposals (
    id integer NOT NULL,
    rfq_id character varying(36) NOT NULL,
    supplier_actor_id integer NOT NULL,
    price double precision NOT NULL,
    currency public.currency NOT NULL,
    delivery_time character varying(100),
    message text,
    status public.proposal_status NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: proposals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.proposals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: proposals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.proposals_id_seq OWNED BY public.proposals.id;


--
-- Name: refresh_token_blacklist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refresh_token_blacklist (
    id integer NOT NULL,
    token_jti_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: refresh_token_blacklist_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.refresh_token_blacklist_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: refresh_token_blacklist_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.refresh_token_blacklist_id_seq OWNED BY public.refresh_token_blacklist.id;


--
-- Name: reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reviews (
    id integer NOT NULL,
    contract_id integer NOT NULL,
    reviewer_actor_id integer NOT NULL,
    target_actor_id integer NOT NULL,
    rating integer NOT NULL,
    comment text,
    created_at timestamp with time zone NOT NULL DEFAULT NOW()
);


--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reviews_id_seq OWNED BY public.reviews.id;


--
-- Name: rfq_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_attachments (
    id character varying(36) NOT NULL,
    rfq_id character varying(36) NOT NULL,
    file_name character varying(255) NOT NULL,
    file_url character varying(500) NOT NULL,
    file_type character varying(100) NOT NULL
);


--
-- Name: rfq_invited_suppliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_invited_suppliers (
    id integer NOT NULL,
    rfq_id character varying(36) NOT NULL,
    supplier_actor_id integer NOT NULL
);


--
-- Name: rfq_invited_suppliers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rfq_invited_suppliers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rfq_invited_suppliers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rfq_invited_suppliers_id_seq OWNED BY public.rfq_invited_suppliers.id;


--
-- Name: rfq_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_reports (
    id integer NOT NULL,
    rfq_id character varying(36) NOT NULL,
    reporter_user_id integer NOT NULL,
    reason public.rfq_report_reason NOT NULL,
    details text,
    status public.rfq_report_status NOT NULL,
    created_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_id integer
);


--
-- Name: rfq_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rfq_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rfq_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rfq_reports_id_seq OWNED BY public.rfq_reports.id;


--
-- Name: rfqs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfqs (
    id character varying(36) NOT NULL,
    actor_id integer NOT NULL,
    created_by character varying(255) NOT NULL,
    type public.rfq_type NOT NULL,
    title character varying(500) NOT NULL,
    description text,
    category_id character varying(100) NOT NULL,
    budget_type public.budget_type NOT NULL,
    budget_from double precision,
    budget_to double precision,
    currency character varying(10) NOT NULL,
    deadline character varying(30) NOT NULL,
    visibility public.rfq_visibility NOT NULL,
    status public.rfq_status NOT NULL,
    quantity integer,
    delivery_country character varying(100),
    delivery_city character varying(100),
    delivery_address character varying(500),
    delivery_date character varying(30),
    project_duration character varying(100),
    start_date character varying(30),
    team_size_required integer,
    experience_required character varying(255),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: supplier_invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier_invoices (
    id integer NOT NULL,
    actor_id integer NOT NULL,
    contract_id integer,
    number character varying(100) NOT NULL,
    title character varying(255) NOT NULL,
    amount double precision NOT NULL,
    currency character varying(10) NOT NULL,
    status public.invoice_status NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    due_at timestamp with time zone,
    paid_at timestamp with time zone
);


--
-- Name: supplier_invoices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.supplier_invoices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: supplier_invoices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.supplier_invoices_id_seq OWNED BY public.supplier_invoices.id;


--
-- Name: supplier_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier_subscriptions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    plan public.subscription_plan NOT NULL,
    active_until timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: supplier_subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.supplier_subscriptions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: supplier_subscriptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.supplier_subscriptions_id_seq OWNED BY public.supplier_subscriptions.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    first_name character varying(100) NOT NULL,
    last_name character varying(100) NOT NULL,
    phone character varying(50),
    role public.user_role NOT NULL,
    status public.user_status NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: withdrawal_destinations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.withdrawal_destinations (
    id integer NOT NULL,
    actor_id integer NOT NULL,
    type public.withdrawal_destination_type NOT NULL,
    label character varying(255) NOT NULL,
    details text NOT NULL,
    is_default boolean NOT NULL
);


--
-- Name: withdrawal_destinations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.withdrawal_destinations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: withdrawal_destinations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.withdrawal_destinations_id_seq OWNED BY public.withdrawal_destinations.id;


--
-- Name: withdrawals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.withdrawals (
    id integer NOT NULL,
    actor_id integer NOT NULL,
    destination_id integer NOT NULL,
    amount double precision NOT NULL,
    currency character varying(10) NOT NULL,
    status public.withdrawal_status NOT NULL,
    created_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: withdrawals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.withdrawals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: withdrawals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.withdrawals_id_seq OWNED BY public.withdrawals.id;


--
-- Name: work_submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.work_submissions (
    id integer NOT NULL,
    contract_id integer NOT NULL,
    type public.submission_type NOT NULL,
    note text NOT NULL,
    status public.submission_status NOT NULL,
    submitted_at timestamp with time zone NOT NULL,
    file_names character varying[] NOT NULL,
    assets jsonb NOT NULL
);


--
-- Name: work_submissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.work_submissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: work_submissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.work_submissions_id_seq OWNED BY public.work_submissions.id;


--
-- Name: actors id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actors ALTER COLUMN id SET DEFAULT nextval('public.actors_id_seq'::regclass);


--
-- Name: audit_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs ALTER COLUMN id SET DEFAULT nextval('public.audit_logs_id_seq'::regclass);


--
-- Name: catalog_item_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_item_reports ALTER COLUMN id SET DEFAULT nextval('public.catalog_item_reports_id_seq'::regclass);


--
-- Name: catalog_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items ALTER COLUMN id SET DEFAULT nextval('public.catalog_items_id_seq'::regclass);


--
-- Name: categories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories ALTER COLUMN id SET DEFAULT nextval('public.categories_id_seq'::regclass);


--
-- Name: companies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies ALTER COLUMN id SET DEFAULT nextval('public.companies_id_seq'::regclass);


--
-- Name: company_categories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_categories ALTER COLUMN id SET DEFAULT nextval('public.company_categories_id_seq'::regclass);


--
-- Name: company_certificates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_certificates ALTER COLUMN id SET DEFAULT nextval('public.company_certificates_id_seq'::regclass);


--
-- Name: company_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_users ALTER COLUMN id SET DEFAULT nextval('public.company_users_id_seq'::regclass);


--
-- Name: contract_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_files ALTER COLUMN id SET DEFAULT nextval('public.contract_files_id_seq'::regclass);


--
-- Name: contracts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts ALTER COLUMN id SET DEFAULT nextval('public.contracts_id_seq'::regclass);


--
-- Name: conversations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations ALTER COLUMN id SET DEFAULT nextval('public.conversations_id_seq'::regclass);


--
-- Name: dispute_evidence id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispute_evidence ALTER COLUMN id SET DEFAULT nextval('public.dispute_evidence_id_seq'::regclass);


--
-- Name: disputes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.disputes ALTER COLUMN id SET DEFAULT nextval('public.disputes_id_seq'::regclass);


--
-- Name: item_attributes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_attributes ALTER COLUMN id SET DEFAULT nextval('public.item_attributes_id_seq'::regclass);


--
-- Name: item_media id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_media ALTER COLUMN id SET DEFAULT nextval('public.item_media_id_seq'::regclass);


--
-- Name: item_pricing id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_pricing ALTER COLUMN id SET DEFAULT nextval('public.item_pricing_id_seq'::regclass);


--
-- Name: item_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_stats ALTER COLUMN id SET DEFAULT nextval('public.item_stats_id_seq'::regclass);


--
-- Name: message_attachments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_attachments ALTER COLUMN id SET DEFAULT nextval('public.message_attachments_id_seq'::regclass);


--
-- Name: messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: payment_milestones id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_milestones ALTER COLUMN id SET DEFAULT nextval('public.payment_milestones_id_seq'::regclass);


--
-- Name: payment_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_plans ALTER COLUMN id SET DEFAULT nextval('public.payment_plans_id_seq'::regclass);


--
-- Name: platform_payments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_payments ALTER COLUMN id SET DEFAULT nextval('public.platform_payments_id_seq'::regclass);


--
-- Name: proposal_attachments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_attachments ALTER COLUMN id SET DEFAULT nextval('public.proposal_attachments_id_seq'::regclass);


--
-- Name: proposal_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_messages ALTER COLUMN id SET DEFAULT nextval('public.proposal_messages_id_seq'::regclass);


--
-- Name: proposal_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_reports ALTER COLUMN id SET DEFAULT nextval('public.proposal_reports_id_seq'::regclass);


--
-- Name: proposals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposals ALTER COLUMN id SET DEFAULT nextval('public.proposals_id_seq'::regclass);


--
-- Name: refresh_token_blacklist id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_token_blacklist ALTER COLUMN id SET DEFAULT nextval('public.refresh_token_blacklist_id_seq'::regclass);


--
-- Name: reviews id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews ALTER COLUMN id SET DEFAULT nextval('public.reviews_id_seq'::regclass);


--
-- Name: rfq_invited_suppliers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_invited_suppliers ALTER COLUMN id SET DEFAULT nextval('public.rfq_invited_suppliers_id_seq'::regclass);


--
-- Name: rfq_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_reports ALTER COLUMN id SET DEFAULT nextval('public.rfq_reports_id_seq'::regclass);


--
-- Name: supplier_invoices id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_invoices ALTER COLUMN id SET DEFAULT nextval('public.supplier_invoices_id_seq'::regclass);


--
-- Name: supplier_subscriptions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_subscriptions ALTER COLUMN id SET DEFAULT nextval('public.supplier_subscriptions_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: withdrawal_destinations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdrawal_destinations ALTER COLUMN id SET DEFAULT nextval('public.withdrawal_destinations_id_seq'::regclass);


--
-- Name: withdrawals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdrawals ALTER COLUMN id SET DEFAULT nextval('public.withdrawals_id_seq'::regclass);


--
-- Name: work_submissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_submissions ALTER COLUMN id SET DEFAULT nextval('public.work_submissions_id_seq'::regclass);


--
-- Name: actors actors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actors
    ADD CONSTRAINT actors_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: catalog_item_reports catalog_item_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_item_reports
    ADD CONSTRAINT catalog_item_reports_pkey PRIMARY KEY (id);


--
-- Name: catalog_items catalog_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items
    ADD CONSTRAINT catalog_items_pkey PRIMARY KEY (id);


--
-- Name: categories categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (id);


--
-- Name: companies companies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT companies_pkey PRIMARY KEY (id);


--
-- Name: company_categories company_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_categories
    ADD CONSTRAINT company_categories_pkey PRIMARY KEY (id);


--
-- Name: company_certificates company_certificates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_certificates
    ADD CONSTRAINT company_certificates_pkey PRIMARY KEY (id);


--
-- Name: company_profiles company_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_profiles
    ADD CONSTRAINT company_profiles_pkey PRIMARY KEY (company_id);


--
-- Name: company_stats company_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_stats
    ADD CONSTRAINT company_stats_pkey PRIMARY KEY (company_id);


--
-- Name: company_users company_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_users
    ADD CONSTRAINT company_users_pkey PRIMARY KEY (id);


--
-- Name: contract_files contract_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_files
    ADD CONSTRAINT contract_files_pkey PRIMARY KEY (id);


--
-- Name: contracts contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_pkey PRIMARY KEY (id);


--
-- Name: contracts contracts_proposal_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_proposal_id_key UNIQUE (proposal_id);


--
-- Name: conversations conversations_contract_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_contract_id_key UNIQUE (contract_id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: dispute_evidence dispute_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispute_evidence
    ADD CONSTRAINT dispute_evidence_pkey PRIMARY KEY (id);


--
-- Name: disputes disputes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.disputes
    ADD CONSTRAINT disputes_pkey PRIMARY KEY (id);


--
-- Name: item_attributes item_attributes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_attributes
    ADD CONSTRAINT item_attributes_pkey PRIMARY KEY (id);


--
-- Name: item_media item_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_media
    ADD CONSTRAINT item_media_pkey PRIMARY KEY (id);


--
-- Name: item_pricing item_pricing_item_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_pricing
    ADD CONSTRAINT item_pricing_item_id_key UNIQUE (item_id);


--
-- Name: item_pricing item_pricing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_pricing
    ADD CONSTRAINT item_pricing_pkey PRIMARY KEY (id);


--
-- Name: item_stats item_stats_item_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_stats
    ADD CONSTRAINT item_stats_item_id_key UNIQUE (item_id);


--
-- Name: item_stats item_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_stats
    ADD CONSTRAINT item_stats_pkey PRIMARY KEY (id);


--
-- Name: message_attachments message_attachments_message_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_attachments
    ADD CONSTRAINT message_attachments_message_id_key UNIQUE (message_id);


--
-- Name: message_attachments message_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_attachments
    ADD CONSTRAINT message_attachments_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: payment_milestones payment_milestones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_milestones
    ADD CONSTRAINT payment_milestones_pkey PRIMARY KEY (id);


--
-- Name: payment_plans payment_plans_contract_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_plans
    ADD CONSTRAINT payment_plans_contract_id_key UNIQUE (contract_id);


--
-- Name: payment_plans payment_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_plans
    ADD CONSTRAINT payment_plans_pkey PRIMARY KEY (id);


--
-- Name: platform_payments platform_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_payments
    ADD CONSTRAINT platform_payments_pkey PRIMARY KEY (id);


--
-- Name: proposal_attachments proposal_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_attachments
    ADD CONSTRAINT proposal_attachments_pkey PRIMARY KEY (id);


--
-- Name: proposal_attachments proposal_attachments_proposal_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_attachments
    ADD CONSTRAINT proposal_attachments_proposal_id_key UNIQUE (proposal_id);


--
-- Name: proposal_messages proposal_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_messages
    ADD CONSTRAINT proposal_messages_pkey PRIMARY KEY (id);


--
-- Name: proposal_reports proposal_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_reports
    ADD CONSTRAINT proposal_reports_pkey PRIMARY KEY (id);


--
-- Name: proposals proposals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposals
    ADD CONSTRAINT proposals_pkey PRIMARY KEY (id);


--
-- Name: refresh_token_blacklist refresh_token_blacklist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_token_blacklist
    ADD CONSTRAINT refresh_token_blacklist_pkey PRIMARY KEY (id);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: rfq_attachments rfq_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_attachments
    ADD CONSTRAINT rfq_attachments_pkey PRIMARY KEY (id);


--
-- Name: rfq_invited_suppliers rfq_invited_suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_invited_suppliers
    ADD CONSTRAINT rfq_invited_suppliers_pkey PRIMARY KEY (id);


--
-- Name: rfq_reports rfq_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_reports
    ADD CONSTRAINT rfq_reports_pkey PRIMARY KEY (id);


--
-- Name: rfqs rfqs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfqs
    ADD CONSTRAINT rfqs_pkey PRIMARY KEY (id);


--
-- Name: supplier_invoices supplier_invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_invoices
    ADD CONSTRAINT supplier_invoices_pkey PRIMARY KEY (id);


--
-- Name: supplier_subscriptions supplier_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_subscriptions
    ADD CONSTRAINT supplier_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: company_categories uq_company_category; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_categories
    ADD CONSTRAINT uq_company_category UNIQUE (company_id, category_id);


--
-- Name: company_users uq_company_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_users
    ADD CONSTRAINT uq_company_user UNIQUE (company_id, user_id);


--
-- Name: reviews uq_review_contract; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT uq_review_contract UNIQUE (contract_id, reviewer_actor_id);


--
-- Name: rfq_invited_suppliers uq_rfq_supplier; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_invited_suppliers
    ADD CONSTRAINT uq_rfq_supplier UNIQUE (rfq_id, supplier_actor_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: withdrawal_destinations withdrawal_destinations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdrawal_destinations
    ADD CONSTRAINT withdrawal_destinations_pkey PRIMARY KEY (id);


--
-- Name: withdrawals withdrawals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdrawals
    ADD CONSTRAINT withdrawals_pkey PRIMARY KEY (id);


--
-- Name: work_submissions work_submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_submissions
    ADD CONSTRAINT work_submissions_pkey PRIMARY KEY (id);


--
-- Name: ix_actors_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actors_company_id ON public.actors USING btree (company_id);


--
-- Name: ix_actors_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actors_user_id ON public.actors USING btree (user_id);


--
-- Name: ix_catalog_item_reports_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_item_reports_item_id ON public.catalog_item_reports USING btree (item_id);


--
-- Name: ix_catalog_item_reports_reporter_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_item_reports_reporter_user_id ON public.catalog_item_reports USING btree (reporter_user_id);


--
-- Name: ix_catalog_item_reports_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_item_reports_status ON public.catalog_item_reports USING btree (status);


--
-- Name: ix_catalog_items_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_items_actor_id ON public.catalog_items USING btree (actor_id);


--
-- Name: ix_categories_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_categories_slug ON public.categories USING btree (slug);


--
-- Name: ix_companies_operational_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_companies_operational_status ON public.companies USING btree (operational_status);


--
-- Name: ix_contracts_buyer_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_buyer_actor_id ON public.contracts USING btree (buyer_actor_id);


--
-- Name: ix_contracts_rfq_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_rfq_id ON public.contracts USING btree (rfq_id);


--
-- Name: ix_contracts_supplier_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_supplier_actor_id ON public.contracts USING btree (supplier_actor_id);


--
-- Name: ix_dispute_evidence_dispute_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispute_evidence_dispute_id ON public.dispute_evidence USING btree (dispute_id);


--
-- Name: ix_dispute_evidence_uploaded_by_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispute_evidence_uploaded_by_actor_id ON public.dispute_evidence USING btree (uploaded_by_actor_id);


--
-- Name: ix_disputes_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_disputes_contract_id ON public.disputes USING btree (contract_id);


--
-- Name: ix_disputes_opened_by_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_disputes_opened_by_actor_id ON public.disputes USING btree (opened_by_actor_id);


--
-- Name: ix_notifications_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_user_id ON public.notifications USING btree (user_id);


--
-- Name: ix_payment_milestones_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payment_milestones_contract_id ON public.payment_milestones USING btree (contract_id);


--
-- Name: ix_platform_payments_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_actor_id ON public.platform_payments USING btree (actor_id);


--
-- Name: ix_platform_payments_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_contract_id ON public.platform_payments USING btree (contract_id);


--
-- Name: ix_platform_payments_external_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_external_id ON public.platform_payments USING btree (external_id);


--
-- Name: ix_platform_payments_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_invoice_id ON public.platform_payments USING btree (invoice_id);


--
-- Name: ix_platform_payments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_status ON public.platform_payments USING btree (status);


--
-- Name: ix_platform_payments_subscription_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_subscription_user_id ON public.platform_payments USING btree (subscription_user_id);


--
-- Name: ix_platform_payments_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_type ON public.platform_payments USING btree (type);


--
-- Name: ix_platform_payments_withdrawal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_platform_payments_withdrawal_id ON public.platform_payments USING btree (withdrawal_id);


--
-- Name: ix_proposal_messages_proposal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposal_messages_proposal_id ON public.proposal_messages USING btree (proposal_id);


--
-- Name: ix_proposal_messages_sender_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposal_messages_sender_id ON public.proposal_messages USING btree (sender_id);


--
-- Name: ix_proposal_reports_proposal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposal_reports_proposal_id ON public.proposal_reports USING btree (proposal_id);


--
-- Name: ix_proposal_reports_reporter_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposal_reports_reporter_user_id ON public.proposal_reports USING btree (reporter_user_id);


--
-- Name: ix_proposal_reports_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposal_reports_status ON public.proposal_reports USING btree (status);


--
-- Name: ix_proposals_rfq_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposals_rfq_id ON public.proposals USING btree (rfq_id);


--
-- Name: ix_proposals_supplier_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_proposals_supplier_actor_id ON public.proposals USING btree (supplier_actor_id);


--
-- Name: ix_refresh_token_blacklist_token_jti_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_refresh_token_blacklist_token_jti_hash ON public.refresh_token_blacklist USING btree (token_jti_hash);


--
-- Name: ix_rfq_reports_reporter_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rfq_reports_reporter_user_id ON public.rfq_reports USING btree (reporter_user_id);


--
-- Name: ix_rfq_reports_rfq_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rfq_reports_rfq_id ON public.rfq_reports USING btree (rfq_id);


--
-- Name: ix_rfq_reports_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rfq_reports_status ON public.rfq_reports USING btree (status);


--
-- Name: ix_rfqs_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rfqs_actor_id ON public.rfqs USING btree (actor_id);


--
-- Name: ix_supplier_invoices_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supplier_invoices_actor_id ON public.supplier_invoices USING btree (actor_id);


--
-- Name: ix_supplier_subscriptions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_supplier_subscriptions_user_id ON public.supplier_subscriptions USING btree (user_id);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: ix_withdrawal_destinations_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_withdrawal_destinations_actor_id ON public.withdrawal_destinations USING btree (actor_id);


--
-- Name: ix_withdrawals_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_withdrawals_actor_id ON public.withdrawals USING btree (actor_id);


--
-- Name: uq_actor_company_side; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_actor_company_side ON public.actors USING btree (company_id, side) WHERE (kind = 'company'::public.actor_kind);


--
-- Name: uq_actor_individual_user_side; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_actor_individual_user_side ON public.actors USING btree (user_id, side) WHERE (kind = 'individual'::public.actor_kind);


--
-- Name: uq_catalog_item_open_report; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_catalog_item_open_report ON public.catalog_item_reports USING btree (item_id, reporter_user_id) WHERE (status = 'open'::public.catalog_item_report_status);


--
-- Name: uq_dispute_active_contract; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_dispute_active_contract ON public.disputes USING btree (contract_id) WHERE (status = ANY (ARRAY['open'::public.dispute_status, 'under_review'::public.dispute_status, 'appealed'::public.dispute_status]));


--
-- Name: uq_proposal_open_report; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_proposal_open_report ON public.proposal_reports USING btree (proposal_id, reporter_user_id) WHERE (status = 'open'::public.proposal_report_status);


--
-- Name: uq_rfq_open_report; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rfq_open_report ON public.rfq_reports USING btree (rfq_id, reporter_user_id) WHERE (status = 'open'::public.rfq_report_status);


--
-- Name: actors actors_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actors
    ADD CONSTRAINT actors_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id);


--
-- Name: actors actors_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actors
    ADD CONSTRAINT actors_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: catalog_item_reports catalog_item_reports_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_item_reports
    ADD CONSTRAINT catalog_item_reports_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.catalog_items(id);


--
-- Name: catalog_item_reports catalog_item_reports_reporter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_item_reports
    ADD CONSTRAINT catalog_item_reports_reporter_user_id_fkey FOREIGN KEY (reporter_user_id) REFERENCES public.users(id);


--
-- Name: catalog_item_reports catalog_item_reports_resolved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_item_reports
    ADD CONSTRAINT catalog_item_reports_resolved_by_id_fkey FOREIGN KEY (resolved_by_id) REFERENCES public.users(id);


--
-- Name: catalog_items catalog_items_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items
    ADD CONSTRAINT catalog_items_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: catalog_items catalog_items_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items
    ADD CONSTRAINT catalog_items_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id);


--
-- Name: categories categories_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.categories(id);


--
-- Name: companies companies_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT companies_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id);


--
-- Name: company_categories company_categories_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_categories
    ADD CONSTRAINT company_categories_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id);


--
-- Name: company_categories company_categories_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_categories
    ADD CONSTRAINT company_categories_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id);


--
-- Name: company_certificates company_certificates_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_certificates
    ADD CONSTRAINT company_certificates_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id);


--
-- Name: company_profiles company_profiles_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_profiles
    ADD CONSTRAINT company_profiles_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id);


--
-- Name: company_stats company_stats_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_stats
    ADD CONSTRAINT company_stats_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id);


--
-- Name: company_users company_users_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_users
    ADD CONSTRAINT company_users_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id);


--
-- Name: company_users company_users_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_users
    ADD CONSTRAINT company_users_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: contract_files contract_files_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_files
    ADD CONSTRAINT contract_files_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: contract_files contract_files_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_files
    ADD CONSTRAINT contract_files_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: contracts contracts_buyer_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_buyer_actor_id_fkey FOREIGN KEY (buyer_actor_id) REFERENCES public.actors(id);


--
-- Name: contracts contracts_proposal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_proposal_id_fkey FOREIGN KEY (proposal_id) REFERENCES public.proposals(id);


--
-- Name: contracts contracts_rfq_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_rfq_id_fkey FOREIGN KEY (rfq_id) REFERENCES public.rfqs(id);


--
-- Name: contracts contracts_supplier_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_supplier_actor_id_fkey FOREIGN KEY (supplier_actor_id) REFERENCES public.actors(id);


--
-- Name: conversations conversations_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: dispute_evidence dispute_evidence_dispute_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispute_evidence
    ADD CONSTRAINT dispute_evidence_dispute_id_fkey FOREIGN KEY (dispute_id) REFERENCES public.disputes(id);


--
-- Name: dispute_evidence dispute_evidence_uploaded_by_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispute_evidence
    ADD CONSTRAINT dispute_evidence_uploaded_by_actor_id_fkey FOREIGN KEY (uploaded_by_actor_id) REFERENCES public.actors(id);


--
-- Name: disputes disputes_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.disputes
    ADD CONSTRAINT disputes_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: disputes disputes_opened_by_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.disputes
    ADD CONSTRAINT disputes_opened_by_actor_id_fkey FOREIGN KEY (opened_by_actor_id) REFERENCES public.actors(id);


--
-- Name: disputes disputes_resolved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.disputes
    ADD CONSTRAINT disputes_resolved_by_id_fkey FOREIGN KEY (resolved_by_id) REFERENCES public.users(id);


--
-- Name: item_attributes item_attributes_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_attributes
    ADD CONSTRAINT item_attributes_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.catalog_items(id) ON DELETE CASCADE;


--
-- Name: item_media item_media_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_media
    ADD CONSTRAINT item_media_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.catalog_items(id) ON DELETE CASCADE;


--
-- Name: item_pricing item_pricing_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_pricing
    ADD CONSTRAINT item_pricing_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.catalog_items(id) ON DELETE CASCADE;


--
-- Name: item_stats item_stats_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_stats
    ADD CONSTRAINT item_stats_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.catalog_items(id) ON DELETE CASCADE;


--
-- Name: message_attachments message_attachments_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_attachments
    ADD CONSTRAINT message_attachments_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id);


--
-- Name: messages messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);


--
-- Name: messages messages_sender_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES public.users(id);


--
-- Name: notifications notifications_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id);


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: payment_milestones payment_milestones_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_milestones
    ADD CONSTRAINT payment_milestones_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: payment_milestones payment_milestones_payment_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_milestones
    ADD CONSTRAINT payment_milestones_payment_plan_id_fkey FOREIGN KEY (payment_plan_id) REFERENCES public.payment_plans(id);


--
-- Name: payment_plans payment_plans_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_plans
    ADD CONSTRAINT payment_plans_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: platform_payments platform_payments_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_payments
    ADD CONSTRAINT platform_payments_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: platform_payments platform_payments_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_payments
    ADD CONSTRAINT platform_payments_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: platform_payments platform_payments_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_payments
    ADD CONSTRAINT platform_payments_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.supplier_invoices(id);


--
-- Name: platform_payments platform_payments_subscription_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_payments
    ADD CONSTRAINT platform_payments_subscription_user_id_fkey FOREIGN KEY (subscription_user_id) REFERENCES public.users(id);


--
-- Name: platform_payments platform_payments_withdrawal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_payments
    ADD CONSTRAINT platform_payments_withdrawal_id_fkey FOREIGN KEY (withdrawal_id) REFERENCES public.withdrawals(id);


--
-- Name: proposal_attachments proposal_attachments_proposal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_attachments
    ADD CONSTRAINT proposal_attachments_proposal_id_fkey FOREIGN KEY (proposal_id) REFERENCES public.proposals(id);


--
-- Name: proposal_messages proposal_messages_proposal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_messages
    ADD CONSTRAINT proposal_messages_proposal_id_fkey FOREIGN KEY (proposal_id) REFERENCES public.proposals(id);


--
-- Name: proposal_messages proposal_messages_sender_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_messages
    ADD CONSTRAINT proposal_messages_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES public.users(id);


--
-- Name: proposal_reports proposal_reports_proposal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_reports
    ADD CONSTRAINT proposal_reports_proposal_id_fkey FOREIGN KEY (proposal_id) REFERENCES public.proposals(id);


--
-- Name: proposal_reports proposal_reports_reporter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_reports
    ADD CONSTRAINT proposal_reports_reporter_user_id_fkey FOREIGN KEY (reporter_user_id) REFERENCES public.users(id);


--
-- Name: proposal_reports proposal_reports_resolved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposal_reports
    ADD CONSTRAINT proposal_reports_resolved_by_id_fkey FOREIGN KEY (resolved_by_id) REFERENCES public.users(id);


--
-- Name: proposals proposals_rfq_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposals
    ADD CONSTRAINT proposals_rfq_id_fkey FOREIGN KEY (rfq_id) REFERENCES public.rfqs(id);


--
-- Name: proposals proposals_supplier_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.proposals
    ADD CONSTRAINT proposals_supplier_actor_id_fkey FOREIGN KEY (supplier_actor_id) REFERENCES public.actors(id);


--
-- Name: reviews reviews_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: reviews reviews_reviewer_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_reviewer_actor_id_fkey FOREIGN KEY (reviewer_actor_id) REFERENCES public.actors(id);


--
-- Name: reviews reviews_target_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_target_actor_id_fkey FOREIGN KEY (target_actor_id) REFERENCES public.actors(id);


--
-- Name: rfq_attachments rfq_attachments_rfq_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_attachments
    ADD CONSTRAINT rfq_attachments_rfq_id_fkey FOREIGN KEY (rfq_id) REFERENCES public.rfqs(id);


--
-- Name: rfq_invited_suppliers rfq_invited_suppliers_rfq_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_invited_suppliers
    ADD CONSTRAINT rfq_invited_suppliers_rfq_id_fkey FOREIGN KEY (rfq_id) REFERENCES public.rfqs(id);


--
-- Name: rfq_invited_suppliers rfq_invited_suppliers_supplier_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_invited_suppliers
    ADD CONSTRAINT rfq_invited_suppliers_supplier_actor_id_fkey FOREIGN KEY (supplier_actor_id) REFERENCES public.actors(id);


--
-- Name: rfq_reports rfq_reports_reporter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_reports
    ADD CONSTRAINT rfq_reports_reporter_user_id_fkey FOREIGN KEY (reporter_user_id) REFERENCES public.users(id);


--
-- Name: rfq_reports rfq_reports_resolved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_reports
    ADD CONSTRAINT rfq_reports_resolved_by_id_fkey FOREIGN KEY (resolved_by_id) REFERENCES public.users(id);


--
-- Name: rfq_reports rfq_reports_rfq_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_reports
    ADD CONSTRAINT rfq_reports_rfq_id_fkey FOREIGN KEY (rfq_id) REFERENCES public.rfqs(id);


--
-- Name: rfqs rfqs_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfqs
    ADD CONSTRAINT rfqs_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: supplier_invoices supplier_invoices_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_invoices
    ADD CONSTRAINT supplier_invoices_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: supplier_invoices supplier_invoices_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_invoices
    ADD CONSTRAINT supplier_invoices_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: supplier_subscriptions supplier_subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_subscriptions
    ADD CONSTRAINT supplier_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: withdrawal_destinations withdrawal_destinations_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdrawal_destinations
    ADD CONSTRAINT withdrawal_destinations_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: withdrawals withdrawals_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdrawals
    ADD CONSTRAINT withdrawals_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: withdrawals withdrawals_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdrawals
    ADD CONSTRAINT withdrawals_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.withdrawal_destinations(id);


--
-- Name: work_submissions work_submissions_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_submissions
    ADD CONSTRAINT work_submissions_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- PostgreSQL database dump complete
--

\unrestrict q1Gsfj3cWf5tNgEfQHCYULIbAG8DAgTes99oZFfE4o84XIDRgdUfQREWQfrguQ2

