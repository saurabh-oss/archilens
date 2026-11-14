"""
External systems, detected from third-party imports.

A system's architecture includes what it talks to: databases, caches, queues,
cloud storage, other companies' APIs. Declaring those by hand in ``.archilens.yml``
is the part most people skip, so L0 used to be a single box. The packages a
codebase imports already say most of it: ``psycopg2`` means PostgreSQL,
``@aws-sdk/client-s3`` means Amazon S3, ``com.stripe`` means Stripe.

This module maps imports that did *not* resolve to a file in the repository onto a
curated catalog. It is deterministic and needs no AI. Only catalog entries become
systems: an ordinary library (``requests``, ``lodash``) is not something the system
talks to, and guessing would put noise on the diagram.

A *generic* entry is evidence of a kind of system without saying which one: an ORM
means "SQL database", ``boto3`` means "AWS". It is dropped when a specific entry of
the same family is also found (``psycopg2`` beside SQLAlchemy is just PostgreSQL).
"""

from __future__ import annotations

from dataclasses import dataclass

from archilens.models import DetectedSystem

#: Which package ecosystem a language's imports belong to.
_ECOSYSTEM = {
    "python": "python",
    "javascript": "npm",
    "typescript": "npm",
    "vue": "npm",
    "svelte": "npm",
    "java": "jvm",
    "kotlin": "jvm",
    "go": "go",
}
#: What separates an import root from what follows it: ``redis.asyncio``, ``pg/lib``.
_SEPARATOR = {"python": ".", "jvm": ".", "npm": "/", "go": "/"}


@dataclass(frozen=True)
class _System:
    name: str
    kind: str  # database | cache | queue | storage | external_api
    family: str = ""  # generic entries yield to specific ones of the same family
    generic: bool = False


def _sys(name: str, kind: str, family: str = "", generic: bool = False) -> _System:
    return _System(name, kind, family, generic)


POSTGRES = _sys("PostgreSQL", "database", "sql")
MYSQL = _sys("MySQL", "database", "sql")
SQLSERVER = _sys("SQL Server", "database", "sql")
ORACLE = _sys("Oracle Database", "database", "sql")
SQL = _sys("SQL database", "database", "sql", generic=True)
MONGO = _sys("MongoDB", "database")
CASSANDRA = _sys("Cassandra", "database")
NEO4J = _sys("Neo4j", "database")
CLICKHOUSE = _sys("ClickHouse", "database")
SNOWFLAKE = _sys("Snowflake", "database")
ELASTIC = _sys("Elasticsearch", "database")
OPENSEARCH = _sys("OpenSearch", "database")
DYNAMODB = _sys("Amazon DynamoDB", "database", "aws")
FIRESTORE = _sys("Firestore", "database")
BIGQUERY = _sys("BigQuery", "database")
COSMOS = _sys("Azure Cosmos DB", "database")
SUPABASE = _sys("Supabase", "database")
REDIS = _sys("Redis", "cache")
MEMCACHED = _sys("Memcached", "cache")
KAFKA = _sys("Kafka", "queue")
RABBITMQ = _sys("RabbitMQ", "queue")
NATS = _sys("NATS", "queue")
SQS = _sys("Amazon SQS", "queue", "aws")
SNS = _sys("Amazon SNS", "queue", "aws")
PUBSUB = _sys("Google Pub/Sub", "queue")
SERVICEBUS = _sys("Azure Service Bus", "queue")
S3 = _sys("Amazon S3", "storage", "aws")
GCS = _sys("Google Cloud Storage", "storage")
AZBLOB = _sys("Azure Blob Storage", "storage")
AWS = _sys("AWS", "external_api", "aws", generic=True)
FIREBASE = _sys("Firebase", "external_api")
STRIPE = _sys("Stripe", "external_api")
TWILIO = _sys("Twilio", "external_api")
SENDGRID = _sys("SendGrid", "external_api")
SLACK = _sys("Slack", "external_api")
GITHUB = _sys("GitHub API", "external_api")
OPENAI = _sys("OpenAI API", "external_api")
ANTHROPIC = _sys("Anthropic API", "external_api")
SENTRY = _sys("Sentry", "external_api")
ALGOLIA = _sys("Algolia", "external_api")
AUTH0 = _sys("Auth0", "external_api")
SMTP = _sys("Email (SMTP)", "external_api")
LDAP = _sys("LDAP directory", "external_api")

#: ecosystem -> {import root: system}. A root matches the import itself and anything below it,
#: and the longest matching root wins. None marks a sub-package that is *not* evidence of the
#: system its parent reveals: gin imports the MongoDB driver's bson package to encode BSON,
#: and never talks to a MongoDB server.
CATALOG: dict[str, dict[str, _System | None]] = {
    "python": {
        **dict.fromkeys(("psycopg2", "psycopg", "asyncpg", "pg8000", "aiopg"), POSTGRES),
        **dict.fromkeys(("MySQLdb", "pymysql", "mysql", "aiomysql", "mariadb"), MYSQL),
        "pymssql": SQLSERVER,
        **dict.fromkeys(("cx_Oracle", "oracledb"), ORACLE),
        **dict.fromkeys(("sqlalchemy", "django.db", "peewee", "tortoise", "sqlmodel", "databases"), SQL),
        **dict.fromkeys(("pymongo", "motor", "mongoengine", "beanie"), MONGO),
        "cassandra": CASSANDRA,
        "neo4j": NEO4J,
        **dict.fromkeys(("clickhouse_driver", "clickhouse_connect"), CLICKHOUSE),
        "snowflake": SNOWFLAKE,
        **dict.fromkeys(("elasticsearch", "elasticsearch_dsl"), ELASTIC),
        "opensearchpy": OPENSEARCH,
        "pynamodb": DYNAMODB,
        **dict.fromkeys(("redis", "aioredis", "rq"), REDIS),
        **dict.fromkeys(("pymemcache", "memcache", "pylibmc"), MEMCACHED),
        **dict.fromkeys(("kafka", "confluent_kafka", "aiokafka"), KAFKA),
        **dict.fromkeys(("pika", "aio_pika"), RABBITMQ),
        "nats": NATS,
        **dict.fromkeys(("boto3", "botocore", "aioboto3", "aiobotocore"), AWS),
        "google.cloud.storage": GCS,
        **dict.fromkeys(("google.cloud.pubsub", "google.cloud.pubsub_v1"), PUBSUB),
        "google.cloud.bigquery": BIGQUERY,
        "google.cloud.firestore": FIRESTORE,
        "firebase_admin": FIREBASE,
        "azure.storage.blob": AZBLOB,
        "azure.servicebus": SERVICEBUS,
        "azure.cosmos": COSMOS,
        "supabase": SUPABASE,
        "stripe": STRIPE,
        "twilio": TWILIO,
        "sendgrid": SENDGRID,
        **dict.fromkeys(("slack_sdk", "slack_bolt"), SLACK),
        **dict.fromkeys(("github", "ghapi"), GITHUB),
        "openai": OPENAI,
        "anthropic": ANTHROPIC,
        "sentry_sdk": SENTRY,
        "algoliasearch": ALGOLIA,
        "auth0": AUTH0,
        **dict.fromkeys(("smtplib", "aiosmtplib"), SMTP),
        **dict.fromkeys(("ldap", "ldap3"), LDAP),
    },
    "npm": {
        **dict.fromkeys(("pg", "postgres", "pg-promise", "@neondatabase/serverless"), POSTGRES),
        **dict.fromkeys(("mysql", "mysql2", "mariadb"), MYSQL),
        **dict.fromkeys(("mssql", "tedious"), SQLSERVER),
        "oracledb": ORACLE,
        **dict.fromkeys(
            ("sequelize", "typeorm", "knex", "@prisma/client", "drizzle-orm", "kysely", "@mikro-orm/core"), SQL
        ),
        **dict.fromkeys(("mongodb", "mongoose"), MONGO),
        "cassandra-driver": CASSANDRA,
        "neo4j-driver": NEO4J,
        "@clickhouse/client": CLICKHOUSE,
        "snowflake-sdk": SNOWFLAKE,
        **dict.fromkeys(("@elastic/elasticsearch", "elasticsearch"), ELASTIC),
        "@opensearch-project/opensearch": OPENSEARCH,
        **dict.fromkeys(("@aws-sdk/client-dynamodb", "@aws-sdk/lib-dynamodb"), DYNAMODB),
        **dict.fromkeys(("redis", "ioredis", "@redis/client", "@upstash/redis", "bullmq", "bull"), REDIS),
        **dict.fromkeys(("memcached", "memjs"), MEMCACHED),
        **dict.fromkeys(("kafkajs", "node-rdkafka", "@confluentinc/kafka-javascript"), KAFKA),
        **dict.fromkeys(("amqplib", "amqp-connection-manager"), RABBITMQ),
        "nats": NATS,
        "@aws-sdk/client-sqs": SQS,
        "@aws-sdk/client-sns": SNS,
        "@aws-sdk/client-s3": S3,
        **dict.fromkeys(("aws-sdk", "@aws-sdk"), AWS),
        "@google-cloud/storage": GCS,
        "@google-cloud/pubsub": PUBSUB,
        "@google-cloud/bigquery": BIGQUERY,
        "@google-cloud/firestore": FIRESTORE,
        **dict.fromkeys(("firebase-admin", "firebase"), FIREBASE),
        "@azure/storage-blob": AZBLOB,
        "@azure/service-bus": SERVICEBUS,
        "@azure/cosmos": COSMOS,
        **dict.fromkeys(("@supabase/supabase-js", "@supabase/ssr"), SUPABASE),
        **dict.fromkeys(("stripe", "@stripe/stripe-js"), STRIPE),
        "twilio": TWILIO,
        "@sendgrid/mail": SENDGRID,
        **dict.fromkeys(("@slack/web-api", "@slack/bolt"), SLACK),
        **dict.fromkeys(("@octokit/rest", "@octokit/core", "octokit"), GITHUB),
        "openai": OPENAI,
        "@anthropic-ai/sdk": ANTHROPIC,
        "@sentry": SENTRY,
        "algoliasearch": ALGOLIA,
        **dict.fromkeys(("auth0", "@auth0"), AUTH0),
        "nodemailer": SMTP,
        "ldapjs": LDAP,
    },
    "jvm": {
        "org.postgresql": POSTGRES,
        **dict.fromkeys(("com.mysql", "org.mariadb.jdbc"), MYSQL),
        "com.microsoft.sqlserver": SQLSERVER,
        "oracle.jdbc": ORACLE,
        **dict.fromkeys(
            (
                "java.sql",
                "javax.sql",
                "jakarta.persistence",
                "javax.persistence",
                "org.springframework.jdbc",
                "org.springframework.data.jpa",
                "org.hibernate",
                "org.jooq",
                "org.apache.ibatis",
                "org.mybatis",
            ),
            SQL,
        ),  # fmt: skip
        **dict.fromkeys(("com.mongodb", "org.springframework.data.mongodb"), MONGO),
        **dict.fromkeys(("com.datastax", "org.springframework.data.cassandra"), CASSANDRA),
        "org.neo4j": NEO4J,
        "com.clickhouse": CLICKHOUSE,
        "net.snowflake": SNOWFLAKE,
        **dict.fromkeys(("org.elasticsearch", "co.elastic.clients", "org.springframework.data.elasticsearch"), ELASTIC),
        "org.opensearch": OPENSEARCH,
        **dict.fromkeys(("com.amazonaws.services.dynamodbv2", "software.amazon.awssdk.services.dynamodb"), DYNAMODB),
        **dict.fromkeys(("redis.clients.jedis", "io.lettuce", "org.springframework.data.redis", "org.redisson"), REDIS),
        "net.spy.memcached": MEMCACHED,
        **dict.fromkeys(("org.apache.kafka", "org.springframework.kafka"), KAFKA),
        **dict.fromkeys(("com.rabbitmq", "org.springframework.amqp"), RABBITMQ),
        "io.nats": NATS,
        **dict.fromkeys(("com.amazonaws.services.sqs", "software.amazon.awssdk.services.sqs"), SQS),
        **dict.fromkeys(("com.amazonaws.services.sns", "software.amazon.awssdk.services.sns"), SNS),
        **dict.fromkeys(("com.amazonaws.services.s3", "software.amazon.awssdk.services.s3"), S3),
        **dict.fromkeys(("com.amazonaws", "software.amazon.awssdk"), AWS),
        "com.google.cloud.storage": GCS,
        "com.google.cloud.pubsub": PUBSUB,
        "com.google.cloud.bigquery": BIGQUERY,
        "com.google.cloud.firestore": FIRESTORE,
        "com.google.firebase": FIREBASE,
        "com.azure.storage.blob": AZBLOB,
        "com.azure.messaging.servicebus": SERVICEBUS,
        "com.azure.cosmos": COSMOS,
        "com.stripe": STRIPE,
        "com.twilio": TWILIO,
        "com.sendgrid": SENDGRID,
        "com.slack.api": SLACK,
        "org.kohsuke.github": GITHUB,
        "com.openai": OPENAI,
        "com.anthropic": ANTHROPIC,
        "io.sentry": SENTRY,
        "com.algolia": ALGOLIA,
        "com.auth0": AUTH0,
        **dict.fromkeys(("jakarta.mail", "javax.mail", "org.springframework.mail"), SMTP),
        **dict.fromkeys(("javax.naming.ldap", "org.springframework.ldap"), LDAP),
    },
    "go": {
        **dict.fromkeys(("github.com/lib/pq", "github.com/jackc/pgx"), POSTGRES),
        "github.com/go-sql-driver/mysql": MYSQL,
        **dict.fromkeys(("github.com/microsoft/go-mssqldb", "github.com/denisenkom/go-mssqldb"), SQLSERVER),
        **dict.fromkeys(
            ("database/sql", "gorm.io/gorm", "github.com/jmoiron/sqlx", "entgo.io/ent", "github.com/uptrace/bun"), SQL
        ),
        "go.mongodb.org/mongo-driver": MONGO,
        **dict.fromkeys(("go.mongodb.org/mongo-driver/bson", "go.mongodb.org/mongo-driver/v2/bson"), None),
        "github.com/gocql/gocql": CASSANDRA,
        "github.com/neo4j/neo4j-go-driver": NEO4J,
        "github.com/ClickHouse/clickhouse-go": CLICKHOUSE,
        "github.com/snowflakedb/gosnowflake": SNOWFLAKE,
        **dict.fromkeys(("github.com/elastic/go-elasticsearch", "github.com/olivere/elastic"), ELASTIC),
        "github.com/opensearch-project/opensearch-go": OPENSEARCH,
        **dict.fromkeys(
            ("github.com/aws/aws-sdk-go-v2/service/dynamodb", "github.com/aws/aws-sdk-go/service/dynamodb"), DYNAMODB
        ),
        **dict.fromkeys(
            ("github.com/redis/go-redis", "github.com/go-redis/redis", "github.com/gomodule/redigo"), REDIS
        ),
        "github.com/bradfitz/gomemcache": MEMCACHED,
        **dict.fromkeys(
            (
                "github.com/segmentio/kafka-go",
                "github.com/IBM/sarama",
                "github.com/Shopify/sarama",
                "github.com/confluentinc/confluent-kafka-go",
            ),
            KAFKA,
        ),  # fmt: skip
        **dict.fromkeys(("github.com/rabbitmq/amqp091-go", "github.com/streadway/amqp"), RABBITMQ),
        "github.com/nats-io/nats.go": NATS,
        **dict.fromkeys(("github.com/aws/aws-sdk-go-v2/service/sqs", "github.com/aws/aws-sdk-go/service/sqs"), SQS),
        **dict.fromkeys(("github.com/aws/aws-sdk-go-v2/service/sns", "github.com/aws/aws-sdk-go/service/sns"), SNS),
        **dict.fromkeys(("github.com/aws/aws-sdk-go-v2/service/s3", "github.com/aws/aws-sdk-go/service/s3"), S3),
        **dict.fromkeys(("github.com/aws/aws-sdk-go-v2", "github.com/aws/aws-sdk-go"), AWS),
        "cloud.google.com/go/storage": GCS,
        "cloud.google.com/go/pubsub": PUBSUB,
        "cloud.google.com/go/bigquery": BIGQUERY,
        "cloud.google.com/go/firestore": FIRESTORE,
        "firebase.google.com/go": FIREBASE,
        "github.com/Azure/azure-sdk-for-go/sdk/storage/azblob": AZBLOB,
        "github.com/Azure/azure-sdk-for-go/sdk/messaging/azservicebus": SERVICEBUS,
        "github.com/Azure/azure-sdk-for-go/sdk/data/azcosmos": COSMOS,
        "github.com/stripe/stripe-go": STRIPE,
        "github.com/twilio/twilio-go": TWILIO,
        "github.com/sendgrid/sendgrid-go": SENDGRID,
        "github.com/slack-go/slack": SLACK,
        "github.com/google/go-github": GITHUB,
        **dict.fromkeys(("github.com/sashabaranov/go-openai", "github.com/openai/openai-go"), OPENAI),
        "github.com/anthropics/anthropic-sdk-go": ANTHROPIC,
        "github.com/getsentry/sentry-go": SENTRY,
        "net/smtp": SMTP,
        "github.com/go-ldap/ldap": LDAP,
    },
}


def match_system(imported: str, language: str) -> tuple[_System, str] | None:
    """The catalog system *imported* reveals and the import root that matched (longest root wins), or None."""
    ecosystem = _ECOSYSTEM.get(language)
    if ecosystem is None:
        return None
    separator = _SEPARATOR[ecosystem]
    best: tuple[_System | None, str] | None = None
    for root, system in CATALOG[ecosystem].items():
        if (imported == root or imported.startswith(root + separator)) and (best is None or len(root) > len(best[1])):
            best = (system, root)
    if best is None or best[0] is None:
        return None
    return best[0], best[1]


def detect_external_systems(
    unresolved: list[tuple[str, str, str]], declared: list[tuple[str, str]] | None = None
) -> list[DetectedSystem]:
    """
    External systems revealed by imports that did not resolve to a repository file.

    Args:
        unresolved: ``(module path, imported path, language)`` for each such import.
        declared: ``(name, type)`` of the systems in the config's ``external_systems``. A detected
            system the config already covers is left out, so nothing is drawn twice: the same
            name ("Postgres" covers "PostgreSQL", "Stripe API" covers "Stripe"), or, for a
            generic entry, any declared system of the same type (a declared "Orders DB" of type
            database covers "SQL database").

    Returns:
        One entry per system, sorted by name, with the import roots that revealed it and
        the modules that import them. A generic entry ("SQL database", "AWS") is also left
        out when a specific system of the same family was found.
    """
    found: dict[str, tuple[_System, set[str], set[str]]] = {}
    for module, imported, language in unresolved:
        hit = match_system(imported, language)
        if hit is None:
            continue
        system, root = hit
        _, roots, modules = found.setdefault(system.name, (system, set(), set()))
        roots.add(root)
        modules.add(module)

    specific_families = {s.family for s, _, _ in found.values() if s.family and not s.generic}
    declared_names = [_key(name) for name, _ in declared or []]
    declared_types = {kind for _, kind in declared or []}

    def covered(system: _System) -> bool:
        key = _key(system.name)
        same_name = any(key.startswith(d) or d.startswith(key) for d in declared_names if d)
        return same_name or (system.generic and system.kind in declared_types)

    return [
        DetectedSystem(name=s.name, type=s.kind, packages=sorted(roots), modules=sorted(modules), generic=s.generic)
        for s, roots, modules in sorted(found.values(), key=lambda item: item[0].name.lower())
        if not (s.generic and s.family in specific_families) and not covered(s)
    ]


def _key(name: str) -> str:
    """A system name reduced to lower-case letters and digits, for comparing "Postgres" with "PostgreSQL"."""
    return "".join(ch for ch in name.lower() if ch.isalnum())
