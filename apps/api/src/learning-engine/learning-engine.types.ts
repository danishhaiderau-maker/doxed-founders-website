/**
 * Learning Engine — shared local types.
 *
 * `RoutingDecisionRow` mirrors the Prisma `RoutingDecision` model. We use a
 * local row type (matching the pattern in the Flight Recorder and Capability
 * Registry modules) so this kernel file compiles cleanly whether or not
 * `@prisma/client` has been regenerated recently.
 */
export type RoutingDecisionRow = {
  id: string;
  requestId: string;
  userId: string;
  workspaceId: string | null;

  intent: string;
  profile: string;

  chosenProvider: string;
  chosenModel: string;

  promptHash: string;

  accepted: boolean | null;
  retried: boolean | null;
  edited: boolean | null;
  rating: number | null;

  createdAt: Date;
};

/**
 * Outcome the Learning Engine reads per row. A row counts as a success iff
 * the founder neither retried within the window nor heavily edited it.
 * `rating` and `accepted` are not used yet — Phase 4.5 will fold them in.
 */
export function computeRowSuccess(row: {
  retried: boolean | null;
  edited: boolean | null;
}): boolean {
  return !row.retried && !row.edited;
}
