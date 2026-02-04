import './submissionStatusDropdown.scss'

import React from 'react'

import Select, { components, type OptionProps, type SingleValueProps, type DropdownIndicatorProps } from 'react-select'
import bem from '#/bem'
import {
  SUBMISSION_STATUS_OPTIONS,
  SUBMISSION_STATUS_OPTIONS_WITH_SHOW_ALL,
  SubmissionStatusAdditionalName,
} from '#/components/submissions/submissionStatus.constants'
import type {
  SubmissionStatusOption,
  SubmissionStatusOptionName,
} from '#/components/submissions/submissionStatus.constants'

interface SubmissionStatusDropdownProps {
  /** Calls back with `value`, not option object */
  onChange: (newValue: SubmissionStatusOptionName) => void
  /** This is the whole option object */
  currentValue: SubmissionStatusOption
  isDisabled?: boolean
  /** For gray background, includes additional option */
  isForHeaderFilter?: boolean
}

export default function SubmissionStatusDropdown(props: SubmissionStatusDropdownProps) {
  // for rendering options as colorful badges
  function CustomOption(innerProps: OptionProps<SubmissionStatusOption>) {
    const badgeModifiers = [String(innerProps.getValue())]
    if (innerProps.isSelected) {
      badgeModifiers.push('selected')
    }

    return (
      <bem.KoboSelect__optionWrapper>
        <bem.KoboSelect__optionBadge m={badgeModifiers}>
          <components.Option {...innerProps} />
        </bem.KoboSelect__optionBadge>
      </bem.KoboSelect__optionWrapper>
    )
  }

  // for rendering the selected value as colorful badge
  function CustomSingleValue({ children, ...innerProps }: SingleValueProps<SubmissionStatusOption>) {
    let value
    const valueArray = innerProps.getValue()
    if (valueArray && valueArray[0]) {
      value = valueArray[0].value
    }

    const badgeModifiers = [String(value)]

    return <bem.KoboSelect__optionBadge m={badgeModifiers}>{children}</bem.KoboSelect__optionBadge>
  }

  // for rendering the selected value as colorful badge
  function CustomDropdownIndicator(innerProps: DropdownIndicatorProps<SubmissionStatusOption>) {
    return (
      <components.DropdownIndicator {...innerProps}>
        <i className='k-icon k-icon-caret-down' />
      </components.DropdownIndicator>
    )
  }

  // clone the original list array
  let optionsArray = SUBMISSION_STATUS_OPTIONS
  if (props.isForHeaderFilter) {
    optionsArray = SUBMISSION_STATUS_OPTIONS_WITH_SHOW_ALL
  }

  const selectClassNames = ['kobo-select', 'kobo-select--submission-status']

  if (props.isForHeaderFilter) {
    selectClassNames.push('kobo-select--for-nonwhite-background')
  }

  return (
    <Select
      components={{
        Option: CustomOption,
        SingleValue: CustomSingleValue,
        DropdownIndicator: CustomDropdownIndicator,
      }}
      isDisabled={props.isDisabled}
      isClearable={false}
      isSearchable={false}
      value={props.currentValue}
      options={optionsArray}
      onChange={(newSelectedOption) => {
        if (
          // This should not happen, as we are dealing with `isClearable={false}`
          newSelectedOption === null ||
          // This should not happen, as we are dealing with not `isMulti`
          Array.isArray(newSelectedOption)
        ) {
          //
          if (props.isForHeaderFilter) {
            props.onChange(SubmissionStatusAdditionalName.show_all)
          }
        }

        if (newSelectedOption !== null && 'value' in newSelectedOption) {
          props.onChange(newSelectedOption.value)
        }
      }}
      className={selectClassNames.join(' ')}
      classNamePrefix='kobo-select'
      menuPlacement='auto'
    />
  )
}
