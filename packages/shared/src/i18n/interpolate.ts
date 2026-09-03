/**
 * `{token}` template interpolation for the platform i18n service (NFM-4179).
 *
 * Single-pass replacement over `{name}` placeholders. Placeholders with no
 * matching parameter are left verbatim so a missing value is visible in the
 * rendered copy (and in copy snapshots) instead of silently vanishing.
 */

export type InterpolateParams = Readonly<Record<string, string | number>>

export function interpolate(
  template: string,
  params: InterpolateParams,
): string {
  return template.replace(
    /\{([a-zA-Z][a-zA-Z0-9_]*)\}/g,
    (match, name: string) => {
      const value = Object.prototype.hasOwnProperty.call(params, name)
        ? params[name]
        : undefined
      return value === undefined ? match : String(value)
    },
  )
}
