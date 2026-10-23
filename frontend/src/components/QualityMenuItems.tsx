import { Menu, useVideoQualityOptions } from '@vidstack/react'
import { defaultLayoutIcons, useDefaultLayoutWord } from '@vidstack/react/player/layouts/default'
import { qualityLabel } from '../lib/quality'

/** Replaces the default quality list, which labels renditions by height. */
export default function QualityMenuItems() {
  const auto = useDefaultLayoutWord('Auto')
  const options = useVideoQualityOptions({ auto })
  if (options.disabled) return null
  const qualities = options.flatMap((option) => option.quality ? [option.quality] : [])
  const Check = defaultLayoutIcons.Menu.RadioCheck
  return <Menu.RadioGroup className="vds-quality-radio-group vds-radio-group rv-quality-radio-group" value={options.selectedValue}>
    {options.map((option) => <Menu.Radio className="vds-quality-radio vds-radio" value={option.value} onSelect={option.select} key={option.value}>
      <Check className="vds-icon" />
      <span className="vds-radio-label">{option.quality ? qualityLabel(option.quality, qualities) : option.label}</span>
      {option.bitrateText && <span className="vds-radio-hint">{option.bitrateText}</span>}
    </Menu.Radio>)}
  </Menu.RadioGroup>
}
