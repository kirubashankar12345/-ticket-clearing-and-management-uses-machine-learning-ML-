from flask import Blueprint, render_template, request, abort
from flask_login import login_required
from services.kb_service import KBService

kb_bp = Blueprint("kb", __name__)


@kb_bp.route("/")
@login_required
def index():
    query = request.args.get("q", "")
    category = request.args.get("category", "")
    if query:
        articles = KBService.search(query)
    elif category:
        articles = KBService.get_published(category=category)
    else:
        articles = KBService.get_published()
    return render_template(
        "shared/knowledge_base.html",
        articles=articles,
        query=query,
        category=category,
    )


@kb_bp.route("/<slug>")
@login_required
def article(slug):
    kb_article = KBService.get_by_slug(slug)
    if kb_article is None or kb_article.status != "published":
        abort(404)
    return render_template("shared/knowledge_base_article.html", article=kb_article)
