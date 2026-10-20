"""PDF materials of a course: table course_document.

Safe to run twice: nothing is created if the table already exists.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    if "course_document" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "course_document",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("public_id", sa.String(length=300), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    if "course_document" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("course_document")
