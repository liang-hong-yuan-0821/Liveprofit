/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConceptMemberDTO } from './ConceptMemberDTO';
/**
 * treemap 概念节点：heat_score = 矩形大小、pct_chg = 矩形颜色（as_of 当日
 * 板块涨跌幅，无行情行 → null）；members = 按 |pct_chg| 降序截断 top 100，
 * member_total 标该概念成分全量数。
 */
export type ConceptTreeNodeDTO = {
    sector_code: string;
    sector_name: string;
    rank: number;
    heat_score: number;
    pct_chg: (number | null);
    member_total: number;
    members: Array<ConceptMemberDTO>;
};

