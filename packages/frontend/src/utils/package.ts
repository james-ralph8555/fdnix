import type { Package } from '../types';

// License object structure from API
interface LicenseInfo {
  type: string;
  shortName: string;
  fullName: string;
  spdxId: string;
  url: string;
  free: boolean;
  redistributable: boolean;
  deprecated: boolean;
}

/**
 * Parse license JSON string from API response
 * @param licenseStr - JSON string containing license info
 * @returns Parsed license info or fallback
 */
export function parseLicense(licenseStr: string): LicenseInfo | null {
  try {
    return JSON.parse(licenseStr) as LicenseInfo;
  } catch (error) {
    console.warn('Failed to parse license:', licenseStr, error);
    return null;
  }
}

/**
 * Get human-readable license name
 * @param licenseStr - JSON string containing license info
 * @returns License display name
 */
export function getLicenseDisplayName(licenseStr: string): string {
  const license = parseLicense(licenseStr);
  return license?.shortName || 'Unknown';
}

/**
 * Get license list for display
 * @param licenseStr - JSON string containing license info
 * @returns Array of license names
 */
export function getLicenseList(licenseStr: string): string[] {
  const license = parseLicense(licenseStr);
  return license ? [license.shortName] : ['Unknown'];
}

/**
 * Check if package is free/libre
 * @param licenseStr - JSON string containing license info
 * @returns Whether the license is free
 */
export function isPackageFree(licenseStr: string): boolean {
  const license = parseLicense(licenseStr);
  return license?.free || false;
}

/**
 * Enhanced package with parsed license info
 * @param pkg - Raw package from API
 * @returns Package with additional parsed fields
 */
export function enhancePackage(pkg: Package): Package {
  const licenseInfo = parseLicense(pkg.license);
  
  return {
    ...pkg,
    licenseList: licenseInfo ? [licenseInfo.shortName] : ['Unknown'],
    unfree: licenseInfo ? !licenseInfo.free : false,
    // Set default values for missing optional fields
    category: pkg.category || 'misc',
    maintainers: pkg.maintainers || [],
    platforms: pkg.platforms || [],
    broken: pkg.broken || false,
  };
}

/**
 * Get install command for a package
 * @param pkg - Package object
 * @returns Installation command
 */
export function getInstallCommand(pkg: Package): string {
  return `nix-env -iA ${pkg.attributePath}`;
}

/**
 * Get shell command for a package
 * @param pkg - Package object  
 * @returns Shell command
 */
export function getShellCommand(pkg: Package): string {
  return `nix-shell -p ${pkg.packageName}`;
}