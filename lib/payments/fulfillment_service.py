# backend/lib/payments/fulfillment_service.py

import logging
import traceback
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import (
    DALGiftcards,
    DALPaymentEvents,
    DALPayments,
    DAOPaymentEventsCreate,
    DAOPaymentsUpdate,
    FilterOp,
    locked_row_by_id,
    safe_transaction,
)
from db.data_models import (
    DAOPayments,
    PaymentEventSource,
    PaymentStatus,
)
from db.data_models.types_ENSURE_BACKWARDS_COMPATIBILITY import (
    ShareCreateRequest,
)
from lib.giftcard.service import grant_giftcard_for_share_fulfilling_payment
from lib.sharing.schemas import ShareCreateResponse
from lib.sharing.service import initialize_shares_and_channels_in_txn
from lib.utils.common import utcnow


async def fulfill_payment_success_if_needed(
    session: AsyncSession,
    *,
    payment_id: UUID,
    audit_source: PaymentEventSource,
    audit_context: Optional[dict[str, str]] = None,
) -> Tuple[Optional[ShareCreateResponse], List[UUID], bool]:
    """
    Public entrypoint used by webhook and eager pollers.

    - Opens a transaction
    - Row-locks the payment to make this a single-runner
    - Skips if already fulfilled (giftcards exist for this payment)
    - Performs DB side-effects atomically:
        * initialize_shares_and_channels_in_txn (idempotent)
        * annotate shares.created_by_payment_id if NULL
        * upsert 'GRANTED' giftcards per share (UNIQUE(share_id) UPSERT)
        * append audit event
    - Returns (share_resp, giftcard_ids, did_fulfill)

    Post-commit enqueue of outbox messages is left to the caller.
    """
    if session.in_transaction():
        raise RuntimeError(
            "[fulfill_payment_success_if_needed] Must be called with no active transaction on the session. "
            "Commit/close the caller transaction first."
        )

    if audit_context is None:
        audit_context = {}

    share_resp: Optional[ShareCreateResponse] = None
    giftcard_ids: List[UUID] = []
    did_fulfill = False

    async with safe_transaction(session, "payments.fulfill_success"):
        # Single-runner: lock the payment row
        async with locked_row_by_id(session, DAOPayments, payment_id) as payment_row:
            # Only fulfill successful payments
            if payment_row.status != PaymentStatus.SUCCEEDED:
                return None, [], False

            if payment_row.fulfilled_at is not None:
                return None, [], False

            if not payment_row.share_create_request:
                logging.error(
                    "[fulfill_success] No share_create_request snapshot on payment %s",
                    payment_id,
                )
                return None, [], False

            # Skip if any giftcard already created for this payment
            already = await DALGiftcards.exists(
                session,
                filters={"created_by_payment_id": (FilterOp.EQ, payment_id)},
            )
            if already:
                return None, [], False

            req: ShareCreateRequest = ShareCreateRequest.deserialize(
                payment_row.share_create_request
            )

            try:
                # 1) Upsert shares/channels (+ outbox)
                share_resp = await initialize_shares_and_channels_in_txn(
                    session=session,
                    user_id=payment_row.created_by_user_id,
                    photobook_id=payment_row.photobook_id,
                    req=req,
                    created_by_payment_id=payment_id,
                )

                # 3) UPSERT a local 'GRANTED' giftcard per share (no provider issuance yet)
                if req.giftcard_request is not None:
                    amt = req.giftcard_request.amount_per_share
                    ccy = req.giftcard_request.currency.lower().strip()
                    brand = req.giftcard_request.brand_code
                    for r in share_resp.recipients:
                        gc_id = await grant_giftcard_for_share_fulfilling_payment(
                            session=session,
                            share_id=r.share_id,
                            payment_id=payment_row.id,
                            user_id=payment_row.created_by_user_id,
                            amount=amt,
                            currency=ccy,
                            brand_code=brand,
                        )
                        if gc_id:
                            giftcard_ids.append(gc_id)

                # 4) Audit fulfillment (append-only)
                await DALPaymentEvents.create(
                    session,
                    DAOPaymentEventsCreate(
                        payment_id=payment_row.id,
                        stripe_event_id=None,
                        event_type="fulfill.success",
                        stripe_event_type=None,
                        source=audit_source,
                        payload={
                            "shares": [
                                {
                                    "share_id": str(r.share_id),
                                    "channels": [
                                        {
                                            "share_channel_id": str(
                                                ch.share_channel_id
                                            ),
                                            "channel_type": ch.channel_type.value,
                                            "destination": ch.destination,
                                        }
                                        for ch in r.share_channel_results
                                    ],
                                    "outbox": [
                                        str(ob.outbox_id) for ob in r.outbox_results
                                    ],
                                }
                                for r in (share_resp.recipients if share_resp else [])
                            ],
                            "giftcard_ids": [str(x) for x in giftcard_ids],
                            **audit_context,
                        },
                        signature_verified=None,
                        applied_status=None,
                    ),
                )

                await DALPayments.update_by_id(
                    session,
                    payment_id,
                    DAOPaymentsUpdate(
                        fulfilled_at=utcnow(),
                        fulfillment_last_error=None,
                    ),
                )

                did_fulfill = True
            except Exception as e:
                did_fulfill = False
                tb = traceback.format_exc()
                await DALPaymentEvents.create(
                    session,
                    DAOPaymentEventsCreate(
                        payment_id=payment_row.id,
                        stripe_event_id=None,
                        event_type="fulfill.failed",
                        stripe_event_type=None,
                        source=audit_source,
                        payload={
                            "error": str(e),
                            "traceback": tb,
                            "shares": [
                                {
                                    "share_id": str(r.share_id),
                                    "channels": [
                                        {
                                            "share_channel_id": str(
                                                ch.share_channel_id
                                            ),
                                            "channel_type": ch.channel_type.value,
                                            "destination": ch.destination,
                                        }
                                        for ch in r.share_channel_results
                                    ],
                                    "outbox": [
                                        str(ob.outbox_id) for ob in r.outbox_results
                                    ],
                                }
                                for r in (share_resp.recipients if share_resp else [])
                            ],
                            "giftcard_ids": [str(x) for x in giftcard_ids],
                            **audit_context,
                        },
                        signature_verified=None,
                        applied_status=None,
                    ),
                )

                await DALPayments.update_by_id(
                    session,
                    payment_id,
                    DAOPaymentsUpdate(
                        fulfilled_at=None,
                        fulfillment_last_error=str(e),
                    ),
                )

    return share_resp, giftcard_ids, did_fulfill
