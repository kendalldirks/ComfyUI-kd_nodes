class AnyType(str):
  def __eq__(self, _):
    return True
  def __ne__(self, _):
    return False