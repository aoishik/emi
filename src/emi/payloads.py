from pydantic import BaseModel, Field


class FulfillOrderPayload(BaseModel):
    user_id: str = Field(min_length=1, description="Slack user ID of order recipient")
    order_id: str = Field(min_length=1)
    item_name: str = Field(min_length=1)
    qty: str = Field(min_length=1)
    cost: str = Field(min_length=1)
    comment: str | None = Field(default=None, max_length=2000)


class FulfillFullfilledPayload(BaseModel):
    user_id: str = Field(min_length=1, description="Slack user ID of order recipient")
    order_id: str = Field(min_length=1)
    item_name: str = Field(min_length=1)
    qty: str = Field(min_length=1)
    cost: str = Field(min_length=1)
    fulfilled_by: str = Field(min_length=1)
    tracking_details: str = Field(min_length=1)


class ReviewAcceptPayload(BaseModel):
    user_id: str = Field(min_length=1, description="Slack user ID of project submitter")
    project_name: str = Field(min_length=1)
    project_link: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1, description="Slack user ID of reviewer")
    feedback: str = Field(min_length=1, max_length=2000)
    # currencies: str = Field(min_length=1, description="Currency reward string e.g. '100 Gold, 50 Silver'")


class ReviewRejectPayload(BaseModel):
    user_id: str = Field(min_length=1, description="Slack user ID of project submitter")
    project_name: str = Field(min_length=1)
    project_link: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1, description="Slack user ID of reviewer")
    feedback: str = Field(min_length=1, max_length=2000)

class ShipPayload(BaseModel):
    user_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    project_link: str = Field(min_length=1)

class VotingCompletePayload(BaseModel):
    user_id: str = Field(min_length=1, description="Slack user ID of project submitter")
    project_name: str = Field(min_length=1)
    project_link: str = Field(min_length=1)
    feedback: str | None = Field(default=None, min_length=1, max_length=2000)
    currencies: str = Field(
        min_length=1, description="Currency reward string e.g. '100 Gold, 50 Silver'"
    )