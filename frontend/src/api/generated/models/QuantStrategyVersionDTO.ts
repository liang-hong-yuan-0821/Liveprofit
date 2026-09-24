/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type QuantStrategyVersionDTO = {
    id: string;
    strategy_id: string;
    version_no: number;
    status: string;
    source_hash: string;
    template_id?: (string | null);
    template_params?: (Record<string, any> | null);
    template_renderer_version?: (string | null);
    lifecycle_policy_version_id?: (string | null);
    published_at: (string | null);
    archived_at: (string | null);
    version: number;
    created_at: string;
    updated_at: string;
};

