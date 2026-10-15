import { errorText } from '../../lib/api';

/**
 * A refusal in words (status, code, the server's value-free message), with the retry it implies
 * (`usePoll` keeps retrying). Used wherever a polled list failed, so a refused or still-loading list
 * is never shown as a confident empty state.
 */
export function Refused({ what, error }: { what: string; error: Error }): JSX.Element {
  return <div className="callout amber" role="alert">The {what} could not be loaded: {errorText(error)}. Retrying.</div>;
}
