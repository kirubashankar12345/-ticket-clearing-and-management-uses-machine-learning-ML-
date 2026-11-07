import logging
from datetime import datetime
from threading import RLock, Thread

from flask import current_app
from sqlalchemy import func, insert, select, text

from models import db
from models.ai_clearing import AiClearingJob, AiClearingJobItem
from models.conversation import Conversation, Message
from models.ticket import Ticket, TicketActivity
from services.ai_service import AIService, CATEGORIES
from services.exceptions import AIServiceUnavailableError
from services.kb_service import KBService
from services.ticket_service import TicketService, VALID_PRIORITIES
from services.ticket_routing_service import TicketRoutingService

logger = logging.getLogger(__name__)
_job_start_lock = RLock()


class AiClearingService:
    BATCH_SIZE = 10
    TERMINAL_ITEM_STATES = (
        "resolved",
        "awaiting_confirmation",
        "transferred",
        "failed",
        "skipped",
    )

    @classmethod
    def start_job(cls, admin_id: int) -> tuple[AiClearingJob, bool]:
        with _job_start_lock:
            return cls._create_job(admin_id)

    @classmethod
    def start_ticket_job(
        cls, employee_id: int, ticket_id: int
    ) -> tuple[AiClearingJob | None, bool]:
        """Automatically process one newly AI-routed ticket without admin action."""
        with _job_start_lock:
            if db.engine.dialect.name == "postgresql":
                db.session.execute(text("SELECT pg_advisory_xact_lock(731904218)"))

            ticket = db.session.get(Ticket, ticket_id)
            if (
                ticket is None
                or ticket.routing_method != "ai"
                or ticket.status in ("Resolved", "Closed")
                or ticket.assigned_engineer_id is not None
            ):
                db.session.commit()
                return None, False

            active_job = db.session.execute(
                select(AiClearingJob)
                .where(AiClearingJob.status.in_(("queued", "running")))
                .order_by(AiClearingJob.id.desc())
                .with_for_update()
            ).scalars().first()
            if active_job:
                existing_item = db.session.execute(
                    select(AiClearingJobItem.id).where(
                        AiClearingJobItem.job_id == active_job.id,
                        AiClearingJobItem.ticket_id == ticket_id,
                    )
                ).scalar_one_or_none()
                if existing_item is None:
                    db.session.add(AiClearingJobItem(
                        job_id=active_job.id,
                        ticket_id=ticket_id,
                        status="pending",
                    ))
                    active_job.total += 1
                    db.session.commit()
                return active_job, False

            return cls._create_job(employee_id, candidate_ticket_ids=[ticket_id])

    @classmethod
    def _create_job(
        cls, started_by: int, candidate_ticket_ids: list[int] | None = None
    ) -> tuple[AiClearingJob, bool]:
        """Create a durable job and launch one worker unless one is already active."""
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text("SELECT pg_advisory_xact_lock(731904218)"))

        active_jobs = select(AiClearingJob).where(
            AiClearingJob.status.in_(("queued", "running"))
        )
        if candidate_ticket_ids is None:
            active_job_query = active_jobs.order_by(
                AiClearingJob.id.desc()
            ).with_for_update()
        else:
            active_job_query = (
                active_jobs.join(AiClearingJobItem)
                .where(AiClearingJobItem.ticket_id.in_(candidate_ticket_ids))
                .order_by(AiClearingJob.id.desc())
                .with_for_update()
            )
        active_job = db.session.execute(active_job_query).scalars().first()
        if active_job:
            return active_job, False

        ticket_id_cutoff = db.session.execute(
            select(func.max(Ticket.id))
        ).scalar_one() or 0
        job = AiClearingJob(
            status="queued",
            started_by=started_by,
            total=0,
        )
        db.session.add(job)
        db.session.flush()
        last_ticket_id = 0
        while True:
            candidate_query = select(Ticket.id).where(
                Ticket.id > last_ticket_id,
                Ticket.id <= ticket_id_cutoff,
                Ticket.status.notin_(("Resolved", "Closed")),
                Ticket.assigned_engineer_id.is_(None),
                Ticket.priority.in_(("Low", "Normal")),
            )
            if candidate_ticket_ids is not None:
                candidate_query = candidate_query.where(Ticket.id.in_(candidate_ticket_ids))
            candidate_query = candidate_query.order_by(Ticket.id).limit(cls.BATCH_SIZE)
            batch_ticket_ids = db.session.execute(
                candidate_query
            ).scalars().all()
            if not batch_ticket_ids:
                break
            db.session.execute(
                insert(AiClearingJobItem),
                [
                    {"job_id": job.id, "ticket_id": ticket_id}
                    for ticket_id in batch_ticket_ids
                ],
            )
            job.total += len(batch_ticket_ids)
            last_ticket_id = batch_ticket_ids[-1]
            db.session.flush()

        job.status = "queued" if job.total else "completed"
        job.started_at = datetime.utcnow() if job.total else None
        job.finished_at = None if job.total else datetime.utcnow()
        db.session.commit()

        if job.total:
            try:
                cls._launch_worker(current_app._get_current_object(), job.id)
            except Exception:
                job.status = "failed"
                job.finished_at = datetime.utcnow()
                db.session.commit()
                logger.exception("Could not start AI clearing job %s.", job.id)
                raise RuntimeError("AI ticket clearing could not be started.")
        return job, True

    @staticmethod
    def latest_job() -> AiClearingJob | None:
        return db.session.execute(
            select(AiClearingJob).order_by(AiClearingJob.id.desc())
        ).scalars().first()

    @staticmethod
    def _launch_worker(app, job_id: int) -> None:
        Thread(
            target=AiClearingService._run_job,
            args=(app, job_id),
            name=f"ai-ticket-clearing-{job_id}",
            daemon=True,
        ).start()

    @classmethod
    def _run_job(cls, app, job_id: int) -> None:
        with app.app_context():
            job = db.session.get(AiClearingJob, job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.started_at = datetime.utcnow()
            db.session.commit()

            try:
                while True:
                    item_ids = db.session.execute(
                        select(AiClearingJobItem.id)
                        .where(
                            AiClearingJobItem.job_id == job_id,
                            AiClearingJobItem.status == "pending",
                        )
                        .order_by(AiClearingJobItem.id)
                        .limit(cls.BATCH_SIZE)
                    ).scalars().all()
                    if not item_ids:
                        if cls._complete_job_if_empty(job_id):
                            break
                        continue

                    for item_id in item_ids:
                        item = db.session.execute(
                            select(AiClearingJobItem)
                            .where(
                                AiClearingJobItem.id == item_id,
                                AiClearingJobItem.status == "pending",
                            )
                            .with_for_update(skip_locked=True)
                        ).scalar_one_or_none()
                        if item is None:
                            db.session.rollback()
                            continue
                        item.status = "processing"
                        item.started_at = datetime.utcnow()
                        ticket_id = item.ticket_id
                        db.session.commit()

                        try:
                            state, message = cls._process_ticket(ticket_id)
                        except Exception as exc:
                            db.session.rollback()
                            logger.error(
                                "Could not finish AI clearing for ticket %s (%s).",
                                ticket_id,
                                type(exc).__name__,
                                exc_info=True,
                            )
                            cls._fallback_after_processing_error(ticket_id)
                            state = "failed"
                            message = (
                                "AI processing failed; the ticket remains in human support."
                            )
                        item = db.session.get(AiClearingJobItem, item_id)
                        item.status = state
                        item.message = message
                        item.finished_at = datetime.utcnow()
                        job = db.session.get(AiClearingJob, job_id)
                        job.processed = db.session.execute(
                            select(func.count(AiClearingJobItem.id)).where(
                                AiClearingJobItem.job_id == job_id,
                                AiClearingJobItem.status.in_(cls.TERMINAL_ITEM_STATES),
                            )
                        ).scalar_one()
                        db.session.commit()

                job = db.session.get(AiClearingJob, job_id)
                job.status = "completed"
                job.finished_at = datetime.utcnow()
                db.session.commit()
            except Exception:
                db.session.rollback()
                job = db.session.get(AiClearingJob, job_id)
                if job:
                    job.status = "failed"
                    job.finished_at = datetime.utcnow()
                    db.session.commit()
                logger.exception("AI clearing job %s failed.", job_id)

    @staticmethod
    def _complete_job_if_empty(job_id: int) -> bool:
        with _job_start_lock:
            if db.engine.dialect.name == "postgresql":
                db.session.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
            pending = db.session.execute(
                select(AiClearingJobItem.id).where(
                    AiClearingJobItem.job_id == job_id,
                    AiClearingJobItem.status == "pending",
                ).limit(1)
            ).scalar_one_or_none()
            job = db.session.get(AiClearingJob, job_id)
            if job is None:
                db.session.rollback()
                return True
            if pending is not None:
                db.session.commit()
                return False
            job.status = "completed"
            job.finished_at = datetime.utcnow()
            db.session.commit()
            return True

    @staticmethod
    def _fallback_after_processing_error(ticket_id: int) -> None:
        try:
            ticket = db.session.get(Ticket, ticket_id)
            if (
                ticket
                and ticket.routing_method == "ai"
                and ticket.status in {"Open", "Waiting for Employee"}
                and ticket.assigned_engineer_id is None
            ):
                TicketService.route_ai_ticket_to_manual(
                    ticket.ticket_id,
                    "AI bulk processing failed; routed to human IT support.",
                )
        except Exception as exc:
            db.session.rollback()
            logger.error(
                "Could not route ticket %s to manual support after AI failure (%s).",
                ticket_id,
                type(exc).__name__,
                exc_info=True,
            )

    @classmethod
    def _process_ticket(cls, ticket_id: int) -> tuple[str, str]:
        ticket = db.session.get(Ticket, ticket_id)
        if (
            ticket is None
            or ticket.status in ("Resolved", "Closed")
            or ticket.assigned_engineer_id is not None
        ):
            return "skipped", "Ticket is no longer eligible for AI clearing."

        if TicketRoutingService.is_critical_issue(ticket.issue_description):
            cls._escalate_critical_ticket(ticket)
            return "transferred", "Critical issue escalated to human support."

        conversation = cls._get_or_create_conversation(ticket)
        db.session.commit()
        messages = db.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc())
        ).scalars().all()
        history = [
            {"role": message.role, "content": message.content}
            for message in messages[-20:]
            if message.role in {"assistant", "user"}
        ]
        prompt = (
            "Troubleshoot this existing support ticket. Use its category and priority "
            "along with its conversation history. Give useful, safe software-support "
            "steps, and ask the employee to confirm whether the issue is resolved. "
            "Do not claim that it is resolved.\n\n"
            f"Ticket category: {ticket.category or 'general'}\n"
            f"Ticket priority: {ticket.priority}\n"
            f"Issue description: {ticket.issue_description}"
        )

        try:
            ai_response = AIService.get_response(
                history,
                prompt,
                KBService.get_context_for_ai(
                    f"{ticket.category or 'general'} {ticket.issue_description}"
                ),
            )
        except AIServiceUnavailableError as exc:
            logger.warning(
                "AI clearing unavailable for ticket %s (%s).",
                ticket.ticket_id,
                type(exc).__name__,
            )
            cls._transfer_to_manual(ticket.ticket_id, conversation, "AI service unavailable.")
            return "transferred", "AI service unavailable; ticket sent to manual support."
        except Exception as exc:
            logger.error(
                "AI clearing failed for ticket %s (%s).",
                ticket.ticket_id,
                type(exc).__name__,
            )
            cls._transfer_to_manual(ticket.ticket_id, conversation, "AI processing failed.")
            return "transferred", "AI could not process the ticket; sent to manual support."

        content = (ai_response.get("content") or "").strip()
        cannot_help = ai_response.get("suggested_ticket") and not ai_response.get(
            "ask_confirmation"
        )
        if not content or cannot_help:
            cls._transfer_to_manual(
                ticket.ticket_id,
                conversation,
                "AI could not provide useful troubleshooting.",
                category=ai_response.get("category")
                if ai_response.get("category") in CATEGORIES
                else None,
                priority=ai_response.get("priority")
                if ai_response.get("priority") in VALID_PRIORITIES
                else None,
                recommendation=content,
            )
            return "transferred", "AI could not provide a useful solution; sent to manual support."

        category = ai_response.get("category")
        if category not in CATEGORIES:
            category = "general"
        priority = ai_response.get("priority")
        if priority not in VALID_PRIORITIES:
            priority = ticket.priority
        if "is your issue resolved?" not in content.lower():
            content += "\n\nIs your issue resolved?"
        content += (
            f"\n\nTicket **{ticket.ticket_id}** is waiting for your confirmation. "
            "Reply **Yes, it's fixed** to resolve it, or **No, still having issues** "
            "to send it to human IT support."
        )

        ticket = db.session.execute(
            select(Ticket).where(Ticket.id == ticket_id).with_for_update()
        ).scalar_one()
        if ticket.status in ("Resolved", "Closed") or ticket.assigned_engineer_id is not None:
            return "skipped", "Ticket was updated by support while AI was processing."
        ticket.routing_method = "ai"
        ticket.routed_at = datetime.utcnow()
        ticket.conversation_id = conversation.id
        ticket.category = category
        ticket.priority = priority
        ticket.ai_recommendation = content[:4000]
        ticket.employee_confirmed = True
        ticket.resolution_notes = content
        ticket.resolution_method = "AI"
        ticket.resolved_by_name = "ResolveAI Automated Engine"
        ticket.resolved_by_role = "AI Support Engine"
        ticket.resolved_by_email = "ai-support@resolveai.com"
        db.session.add(Message(
            conversation_id=conversation.id,
            role="assistant",
            content=content,
        ))
        conversation.active_ticket_id = ticket.ticket_id
        conversation.updated_at = datetime.utcnow()
        db.session.commit()

        # Clear and resolve the ticket via TicketService
        try:
            TicketService.set_status(
                ticket.ticket_id,
                "Resolved",
                "system",
                note="Small issue analyzed and cleared by ResolveAI Automated Engine.",
                resolution_notes=content,
            )
        except Exception as _res_err:
            logger.warning("Could not set status Resolved via TicketService: %s", _res_err)

        return "resolved", "Small issue analyzed and cleared by ResolveAI."

    @staticmethod
    def _get_or_create_conversation(ticket: Ticket) -> Conversation:
        conversation = None
        if ticket.conversation_id:
            conversation = db.session.get(Conversation, ticket.conversation_id)
            if conversation and conversation.employee_id != ticket.employee_id:
                conversation = None
        if conversation is None:
            conversation = Conversation(
                employee_id=ticket.employee_id,
                active_ticket_id=ticket.ticket_id,
            )
            db.session.add(conversation)
            db.session.flush()
            ticket.conversation_id = conversation.id
            db.session.add(Message(
                conversation_id=conversation.id,
                role="user",
                content=ticket.issue_description,
            ))
        else:
            conversation.active_ticket_id = ticket.ticket_id
        return conversation

    @staticmethod
    def _transfer_to_manual(
        ticket_id: int,
        conversation: Conversation,
        reason: str,
        category: str = None,
        priority: str = None,
        recommendation: str = None,
    ) -> None:
        ticket = db.session.get(Ticket, ticket_id)
        if ticket and ticket.status == "Open" and ticket.assigned_engineer_id is None:
            TicketService.route_ai_ticket_to_manual(
                ticket.ticket_id,
                reason,
                category=category,
                priority=priority,
                recommendation=recommendation,
            )
        reply = (recommendation or "").strip()[:4000]
        if reply:
            reply += "\n\n"
        reply += (
            "ResolveAI could not complete troubleshooting, so this ticket has been "
            "sent to human IT support."
        )
        db.session.add(Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
        ))
        conversation.updated_at = datetime.utcnow()
        db.session.commit()

    @staticmethod
    def _escalate_critical_ticket(ticket: Ticket) -> None:
        old_status = ticket.status
        ticket.routing_method = "manual"
        ticket.priority = "Urgent"
        ticket.status = "Escalated"
        ticket.routing_reason = (
            "Critical security incident or production outage escalated directly to human support."
        )
        ticket.updated_at = datetime.utcnow()
        db.session.add(TicketActivity(
            ticket_id=ticket.id,
            changed_by="system",
            old_status=old_status,
            new_status="Escalated",
            note=ticket.routing_reason,
            is_internal=False,
        ))
        db.session.commit()
        TicketService._send_status_notifications(ticket, old_status, "Escalated")
        TicketService._notify_routing_fallback(ticket, ticket.routing_reason)

    @classmethod
    def job_status(cls, job_id: int) -> dict | None:
        job = db.session.get(AiClearingJob, job_id)
        if job is None:
            return None
        resolved = db.session.execute(
            select(func.count(AiClearingJobItem.id))
            .join(Ticket, Ticket.id == AiClearingJobItem.ticket_id)
            .where(
                AiClearingJobItem.job_id == job_id,
                Ticket.status == "Resolved",
                Ticket.resolution_method == "AI",
            )
        ).scalar_one()
        awaiting = db.session.execute(
            select(func.count(AiClearingJobItem.id))
            .join(Ticket, Ticket.id == AiClearingJobItem.ticket_id)
            .where(
                AiClearingJobItem.job_id == job_id,
                AiClearingJobItem.status == "awaiting_confirmation",
                Ticket.status == "Waiting for Employee",
            )
        ).scalar_one()
        return {
            "id": job.id,
            "status": job.status,
            "total": job.total,
            "processed": job.processed,
            "resolved_by_ai": resolved,
            "awaiting_confirmation": awaiting,
            "transferred_to_manual": db.session.execute(
                select(func.count(AiClearingJobItem.id)).where(
                    AiClearingJobItem.job_id == job_id,
                    AiClearingJobItem.status == "transferred",
                )
            ).scalar_one(),
            "failed": db.session.execute(
                select(func.count(AiClearingJobItem.id)).where(
                    AiClearingJobItem.job_id == job_id,
                    AiClearingJobItem.status == "failed",
                )
            ).scalar_one(),
            "progress_percent": (
                round(job.processed * 100 / job.total) if job.total else 100
            ),
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }

    @classmethod
    def clear_ticket_with_ai(cls, ticket_id: int, auto_resolve: bool = True) -> tuple[bool, str]:
        """Directly analyze and clear a ticket using the AI troubleshooting pipeline."""
        ticket = db.session.get(Ticket, ticket_id)
        if not ticket:
            return False, "Ticket not found."
        if ticket.status in ("Resolved", "Closed"):
            return False, f"Ticket is already {ticket.status}."

        if ticket.priority in ("High", "Urgent") or TicketRoutingService.is_critical_issue(ticket.issue_description):
            cls._escalate_critical_ticket(ticket)
            return False, f"Ticket #{ticket.ticket_id} is {ticket.priority} priority and requires manual human clearance. AI clearing is reserved for simple (Low/Normal) tickets."

        ticket.routing_method = "ai"
        ticket.routed_at = datetime.utcnow()

        conversation = cls._get_or_create_conversation(ticket)
        db.session.commit()

        messages = db.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc())
        ).scalars().all()
        history = [
            {"role": message.role, "content": message.content}
            for message in messages[-20:]
            if message.role in {"assistant", "user"}
        ]
        prompt = (
            "Troubleshoot and provide the complete solution for this support ticket. "
            "Use its category and priority along with any conversation history. "
            "Give clear, step-by-step technical instructions to resolve the issue completely.\n\n"
            f"Ticket category: {ticket.category or 'general'}\n"
            f"Ticket priority: {ticket.priority}\n"
            f"Issue description: {ticket.issue_description}"
        )

        try:
            ai_response = AIService.get_response(
                history,
                prompt,
                KBService.get_context_for_ai(
                    f"{ticket.category or 'general'} {ticket.issue_description}"
                ),
            )
        except Exception as exc:
            cls._transfer_to_manual(ticket.id, conversation, f"AI processing failed: {exc}")
            return False, f"AI processing failed: {exc}"

        content_resp = (ai_response.get("content") or "").strip()
        if not content_resp:
            cls._transfer_to_manual(ticket.id, conversation, "AI could not generate a solution.")
            return False, "AI could not generate a solution."

        category = ai_response.get("category")
        if category not in CATEGORIES:
            category = "general"
        priority = ai_response.get("priority")
        if priority not in VALID_PRIORITIES:
            priority = ticket.priority

        ticket.category = category
        ticket.priority = priority
        ticket.ai_recommendation = content_resp[:4000]

        db.session.add(Message(
            conversation_id=conversation.id,
            role="assistant",
            content=content_resp,
        ))
        conversation.active_ticket_id = ticket.ticket_id
        conversation.updated_at = datetime.utcnow()

        if auto_resolve:
            ticket.employee_confirmed = True
            ticket.resolution_notes = content_resp
            TicketService.set_status(
                ticket.ticket_id,
                "Resolved",
                "system",
                note="Ticket cleared and resolved by ResolveAI with knowledge base troubleshooting.",
                resolution_notes=content_resp,
            )
            return True, "Ticket successfully analyzed and cleared by ResolveAI."
        else:
            TicketService.record_ai_result(ticket.ticket_id, category, priority, content_resp)
            return True, "Ticket troubleshooting provided by AI; awaiting employee confirmation."
