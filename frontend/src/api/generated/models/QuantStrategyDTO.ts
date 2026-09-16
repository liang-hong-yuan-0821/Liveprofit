/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { QuantStrategyVersionDTO } from './QuantStrategyVersionDTO';
export type QuantStrategyDTO = {
    id: string;
    name: string;
    description: (string | null);
    version: number;
    created_at: string;
    updated_at: string;
    versions: Array<QuantStrategyVersionDTO>;
};

