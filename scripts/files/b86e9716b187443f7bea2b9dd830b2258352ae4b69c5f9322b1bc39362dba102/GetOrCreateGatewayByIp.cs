/// <summary>
/// Gets the gateway identified by the sender's IP address, or creates it if it does not exist.
/// </summary>
/// <param name="sender">The sender's IPv4 address and port.</param>
/// <param name="media">The communication media.</param>
/// <param name="data">The received data.</param>
/// <returns>The existing or newly created gateway.</returns>

public GXGateway? GetOrCreateGatewayByIp(
    string sender,
    IGXMedia media,
    byte[] data)
{
    if (!System.Net.IPEndPoint.TryParse(sender, out var endpoint))
    {
        throw new ArgumentException(
            "Sender must contain a valid IP address and port.",
            nameof(sender));
    }

    string identifier = endpoint.Address.ToString();
    var gateway = new GXGateway
    {
        Identifier = identifier
    };

    var existing = Ami.SingleOrDefault(gateway);
    if (existing != null)
    {
        return existing;
    }

    gateway.Name = identifier;
    Ami.Add(gateway);
    return gateway;
}