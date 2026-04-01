/**
 * These are the names of submission statuses that the Back end understands.
 */
export enum SubmissionStatusName {
  pending = 'pending',
  completed = 'completed',
}

/**
 * These are the names of additional options for `SubmissionStatusDropdown`.
 */
export enum SubmissionStatusAdditionalName {
  show_all = 'show_all',
}

/**
 * These are all names of all options for `SubmissionStatusDropdown`.
 */
export type SubmissionStatusOptionName = SubmissionStatusName | SubmissionStatusAdditionalName

export interface SubmissionStatusOption {
  value: SubmissionStatusOptionName
  label: string
}

/**
 * Additional option for `SubmissionStatusDropdown`, it's the one for when it's
 * being used as a table header filter.
 */
export const SUBMISSION_STATUS_SHOW_ALL_OPTION: SubmissionStatusOption = {
  value: SubmissionStatusAdditionalName.show_all,
  label: t('Show All'),
}

/** List of options for `SubmissionStatusDropdown` */
export const SUBMISSION_STATUS_OPTIONS: SubmissionStatusOption[] = [
  {
    value: SubmissionStatusName.pending,
    label: t('En curs'),
  },
  {
    value: SubmissionStatusName.completed,
    label: t('Presentat'),
  },
]

/**
 * List of options for `SubmissionStatusDropdown`, including the additional one.
 */
export const SUBMISSION_STATUS_OPTIONS_WITH_SHOW_ALL = [
  SUBMISSION_STATUS_SHOW_ALL_OPTION,
  ...SUBMISSION_STATUS_OPTIONS,
]
