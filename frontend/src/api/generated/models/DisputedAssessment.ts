/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DisputedAssessment = {
    assessment_id: string;
    news_id: string;
    event_id: number;
    fact_key: string;
    revision: number;
    novelty: DisputedAssessment.novelty;
    review_status: string;
    labels: Record<string, any>;
    evidence: Array<Record<string, any>>;
    available_at: string;
    title: string;
    raw_content: string;
    source: string;
    source_label?: (string | null);
    source_url?: (string | null);
};
export namespace DisputedAssessment {
    export enum novelty {
        NEW = 'new',
        UPDATE = 'update',
    }
}

