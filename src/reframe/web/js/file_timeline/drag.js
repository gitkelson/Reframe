export const FILE_DRAG_TYPE = 'application/x-reframe-file-id';

export function isFileDrag(dataTransfer) {
  return !!dataTransfer && [...dataTransfer.types].includes(FILE_DRAG_TYPE);
}
