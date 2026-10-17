/** Optional experiment thresholds are configurable, not an optimization claim. */
export interface EngagementState {
  activeMs: number;
  idleMs: number;
  hidden: boolean;
  focusedForm: boolean;
  dismissed: boolean;
  converted: boolean;
  prompted: boolean;
}
export function mayPrompt(
  s: EngagementState,
  dwellMs = 30000,
  idleMs = 12000
): boolean {
  return (
    s.activeMs >= dwellMs &&
    s.idleMs >= idleMs &&
    !s.hidden &&
    !s.focusedForm &&
    !s.dismissed &&
    !s.converted &&
    !s.prompted
  );
}
export function supportChoices(page: string): string[] {
  if (page === 'pricing')
    return ['Estimate cost', 'Compare plans', 'Cloud versus self-hosted'];
  if (page === 'signup')
    return ['Help with setup', 'Security questions', 'Contact the team'];
  if (page === 'home')
    return ['Understand Preloop', 'Explore a use case', 'See a demo'];
  return ['Inspect my setup', 'Read the documentation', 'Talk to a person'];
}
