"""Separate human logins from companies; preserve legacy IDs and learning data.

Legacy restricted courses remain discoverable-by-request: migration must not
silently change existing learners' access. Revision 0009 restores the same policy for all private courses.
Downgrade is intentionally refused: many companies per person cannot fit the old schema.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "company",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("avatar", sa.String(255)),
        sa.Column("plan", sa.String(20), nullable=False, server_default="free"),
        sa.Column("course_credits", sa.Integer(), nullable=False, server_default="0"),
    )
    connection = op.get_bind()
    # Keep company URLs and all Course/Offer company IDs stable.
    connection.execute(
        sa.text(
            """INSERT INTO company (id, owner_id, name, description, avatar, plan, course_credits)
        SELECT u.id, u.id, p.name, p.description, u.avatar, p.plan, p.course_credits
        FROM "user" u JOIN company_profile p ON p.user_id=u.id"""
        )
    )
    connection.execute(
        sa.text(
            """UPDATE "user" SET avatar=NULL WHERE id IN (SELECT owner_id FROM company)"""
        )
    )
    op.create_table(
        "membership",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.UniqueConstraint("company_id", "user_id", name="uq_membership_company_user"),
        sa.CheckConstraint("role IN ('owner', 'editor')", name="ck_membership_role"),
    )
    connection.execute(
        sa.text(
            "INSERT INTO membership (company_id,user_id,role) SELECT id,owner_id,'owner' FROM company"
        )
    )
    op.create_table(
        "course_editor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False
        ),
        sa.Column(
            "membership_id",
            sa.Integer(),
            sa.ForeignKey("membership.id"),
            nullable=False,
        ),
        sa.UniqueConstraint("course_id", "membership_id", name="uq_course_editor"),
    )
    op.create_table(
        "company_invitation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    for table in ("course", "offer"):
        foreign_key = next(
            f
            for f in sa.inspect(connection).get_foreign_keys(table)
            if f["constrained_columns"] == ["company_id"]
        )
        old_name = foreign_key["name"] or f"fk_{table}_company_id_user"
        with op.batch_alter_table(table, naming_convention=convention) as batch:
            batch.drop_constraint(old_name, type_="foreignkey")
            batch.create_foreign_key(
                f"fk_{table}_company_id_company", "company", ["company_id"], ["id"]
            )
    with op.batch_alter_table("course") as batch:
        batch.add_column(
            sa.Column(
                "access_mode", sa.String(20), nullable=False, server_default="public"
            )
        )
    connection.execute(
        sa.text("UPDATE course SET access_mode='request' WHERE is_private=true")
    )
    with op.batch_alter_table("lesson") as batch:
        batch.add_column(
            sa.Column(
                "video_delivery",
                sa.String(20),
                nullable=False,
                server_default="authenticated",
            )
        )
    connection.execute(
        sa.text(
            "UPDATE lesson SET video_delivery='upload' WHERE video_filename IS NOT NULL"
        )
    )
    # Existing public Cloudinary links cannot be revoked by a database migration.
    # Quarantine restricted courses until their videos have been re-uploaded securely.
    connection.execute(sa.text("""UPDATE course SET status='draft' WHERE is_private=true
        AND id IN (SELECT course_id FROM lesson WHERE video_filename IS NOT NULL)"""))
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('company','id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM company"
            )
        )


def downgrade():
    raise RuntimeError(
        "Migration 0008 cannot be safely reversed. Restore the pre-migration backup instead."
    )
